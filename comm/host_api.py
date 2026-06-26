# comm/host_api.py — UART1 串口通信协议栈
#
# 实现自定义二进制协议：
#   帧头(5A) + 源地址(A7) + 目标地址(97) + 数据长度 + 类型 + 数据 + 校验 + 帧尾(A5)
#   校验 = 从帧头到数据最后一位逐字节累加取低8位
#
# 握手流程（见 通讯协议.txt）：
#   主机发送: 5A 97 98 0B 09 [Please Link...] XX A5
#   摄像头应答: 5A A7 97 0F 09 [Play Application...] XX A5
#   握手成功后主机不再发握手帧，摄像头按当前脚本类型周期推送数据。
#
# 所有 stream 模式脚本 APP 通过 ctx.host 调用本接口发送数据。
# 类型码见 通讯协议.txt 类型表（0x01主菜单 ~ 0x0B图像分类）。

from machine import UART


class HostAPI:
    """上位机串口通信接口（UART1, 115200-8-N-1）"""

    # ── 协议常量 ──
    FRAME_HEAD = 0x5A
    FRAME_TAIL = 0xA5
    SRC_ADDR = 0xA7
    DST_ADDR = 0x97

    # 类型码（对齐 通讯协议.txt）
    TYPE_MAIN_MENU      = 0x01
    TYPE_CAMERA         = 0x02
    TYPE_FACE_DETECT    = 0x03
    TYPE_TAG_DETECT     = 0x04
    TYPE_OBJECT_DETECT  = 0x05
    TYPE_COLOR_DETECT   = 0x06
    TYPE_ROAD_DETECT    = 0x07
    TYPE_GESTURE_DETECT = 0x08
    TYPE_BODY_DETECT    = 0x09
    TYPE_OBJECT_CLASSIFY = 0x0A
    TYPE_IMAGE_CLASSIFY  = 0x0B

    # category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
    CATEGORY_TYPE = {
        "main_menu":  TYPE_MAIN_MENU,     # 0x01
        "settings":   TYPE_MAIN_MENU,     # 0x01（复用主菜单）
        "camera":     TYPE_CAMERA,        # 0x02
        "face_detect":TYPE_FACE_DETECT,   # 0x03
        "tag_detect": TYPE_TAG_DETECT,    # 0x04
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }

    # 握手相关
    HANDSHAKE_CMD = 0x09
    HANDSHAKE_REPLY_PAYLOAD = b"Play Aplication"  # 对齐主机硬匹配(文档拼写,缺一个p)
    # 主机握手请求完整帧(参考 DurUI.USART1_START_FRAME):0x5A 对齐匹配,不用子串。
    # 帧尾 A5 A5(两个)。源97/目98 是主机侧地址,主机 dataAgreeAnalys 不校验地址。
    HANDSHAKE_REQUEST_FRAME = b"\x5A\x97\x98\x0B\x09Please Link\xA5\xA5"
    # 握手应答后静默期:应答后等 100ms 才开始发数据帧(用户确认)。
    HANDSHAKE_COOLDOWN_MS = 100

    def __init__(self):
        # UART1: TX=GPIO40, RX=GPIO41（FPIOA 由 main.py 配置）
        # K230 的 UART 引脚由 FPIOA 统一管理，构造时不传 Pin 对象
        self._uart = UART(UART.UART1, baudrate=115200,
                          bits=UART.EIGHTBITS, parity=UART.PARITY_NONE,
                          stop=UART.STOPBITS_ONE)
        self._connected = False
        self._rx_buf = bytearray()
        self._last_handshake_ms = 0  # 上次应答握手的时间戳(0=从未应答)
        self._handlers = {}

    # ── 公开属性 ──

    @property
    def connected(self):
        return self._connected

    # ── 帧组装与发送 ──

    @staticmethod
    def _checksum(data):
        """计算校验和：所有字节累加取低8位"""
        s = 0
        for b in data:
            s = (s + b) & 0xFF
        return s

    def send_frame(self, msg_type, payload=b''):
        """组装并发送完整协议帧。

        Args:
            msg_type: 类型码 (int, 1字节)
            payload: 负载数据 (bytes)
        """
        inner = bytearray([msg_type]) + bytes(payload)
        length = len(payload)  # 主机 dataAgreeAnalys: data[3]=length 只算 payload,type 在 data[4] 不计入
        header = bytearray([self.FRAME_HEAD, self.SRC_ADDR,
                            self.DST_ADDR, length])
        chk = self._checksum(header + inner)
        frame = bytes(header) + bytes(inner) + bytearray([chk, self.FRAME_TAIL])
        try:
            self._uart.write(frame)
        except Exception as e:
            print(f"[HostAPI] send_frame error: {e}")
            self._connected = False

    def send_face_data(self, slots):
        """发送4组人脸识别数据（类型0x03）。薄封装 → send_id_data。

        Args:
            slots: list of 4 tuples or None. 详见 send_id_data。
        """
        self.send_id_data(self.TYPE_FACE_DETECT, slots)

    def send_id_data(self, msg_type, slots=None):
        """发送4组ID数据（泛化 send_face_data，所有脚本共用）。

        Args:
            msg_type: 类型码 (int, 1字节)
            slots: list[4]，每元素 None 或 (id,x,y,w,h,conf)。
                   None / 越界 → 该组全0。
                   每组 10 字节: id(1B) + x(2B BE) + y(2B BE)
                                + w(2B BE) + h(2B BE) + conf(1B)
                   ⚠️ 大端(BE):对齐主机 _camer_cam_data 大端解析
                   (data[off+1]<<8)|data[off+2]。小端会致坐标值错乱。
                   总计 40 字节数据载荷。
        """
        buf = bytearray(40)
        for i in range(4):
            off = i * 10
            slot = slots[i] if (slots is not None and i < len(slots)) else None
            if slot is not None:
                fid, x, y, w, h, conf = slot
                buf[off]     = fid & 0xFF
                buf[off + 1] = (x >> 8) & 0xFF  # 大端:高字节在前
                buf[off + 2] = x & 0xFF
                buf[off + 3] = (y >> 8) & 0xFF
                buf[off + 4] = y & 0xFF
                buf[off + 5] = (w >> 8) & 0xFF
                buf[off + 6] = w & 0xFF
                buf[off + 7] = (h >> 8) & 0xFF
                buf[off + 8] = h & 0xFF
                buf[off + 9] = conf & 0xFF
            # else: 保持 0（未使用槽位全0）
        self.send_frame(msg_type, bytes(buf))

    def tick(self, category_id, slots=None):
        """每帧调：握手轮询 + 按 category 推送4组数据。

        Args:
            category_id: reset 框架 category（"main_menu"/"camera"/...）
            slots: list[4] 或 None。None → 4组全0（主菜单/相机/settings）。
        """
        self.poll_handshake()
        # 握手应答后 100ms 静默期:不足 100ms 跳过数据帧发送(用户确认)。
        # 主机超时检测的是"有没有收到数据帧";持续发数据帧则主机不重发握手。
        if self._last_handshake_ms != 0:
            import time as _time
            if _time.ticks_diff(_time.ticks_ms(), self._last_handshake_ms) < self.HANDSHAKE_COOLDOWN_MS:
                return
        msg_type = self.CATEGORY_TYPE.get(category_id, self.TYPE_MAIN_MENU)
        self.send_id_data(msg_type, slots)

    # ── 握手状态机 ──

    def poll_handshake(self):
        """非阻塞握手检测：按完整握手请求帧匹配→自动应答。

        应在每帧由 tick() 调用。在 0x5A 对齐边界上匹配完整
        HANDSHAKE_REQUEST_FRAME(参考 DurUI._usart1_try_handshake),不用子串。
        主机超时(没收到数据帧)后会重发握手请求 → 重新应答 + 重置 100ms 计时,
        故入口不短路(_connected 仅作状态标记)。
        """
        try:
            n = self._uart.any()
        except Exception:
            self._connected = False
            return

        if n == 0:
            return

        try:
            raw = self._uart.read(n)
        except Exception:
            return

        if raw is None:
            return

        self._rx_buf.extend(raw)
        # 缓冲区超长只保留尾部(丢老数据,保留最近的帧边界)
        if len(self._rx_buf) > 256:
            self._rx_buf = self._rx_buf[-256:]

        frame = self.HANDSHAKE_REQUEST_FRAME
        flen = len(frame)
        buf = self._rx_buf
        nbuf = len(buf)
        i = 0
        matched_at = -1
        while i <= nbuf - flen:
            if buf[i] == self.FRAME_HEAD and buf[i:i + flen] == frame:
                matched_at = i
                break
            i += 1

        if matched_at < 0:
            # 未匹配:保留从最后一个 0x5A 开始的尾部(可能是半帧,等下次拼)
            # MicroPython bytearray.rfind 不接受 int,须传 bytes
            last_head = buf.rfind(bytes([self.FRAME_HEAD]))
            if last_head > 0:
                self._rx_buf = bytearray(buf[last_head:])
            else:
                self._rx_buf = bytearray()
            return

        # 匹配:应答 + 记时间戳 + 消费掉匹配帧及之前的字节
        self._send_handshake_reply()
        self._rx_buf = bytearray(buf[matched_at + flen:])

    def _send_handshake_reply(self):
        """发送握手应答帧 + 记录应答时间戳(100ms 静默期起点)"""
        import time as _time
        self.send_frame(self.HANDSHAKE_CMD, self.HANDSHAKE_REPLY_PAYLOAD)
        self._connected = True
        self._last_handshake_ms = _time.ticks_ms()
        print("[HostAPI] handshake reply sent — connected")

    # ── 命令注册（预留）──

    def register_handler(self, cmd, callback):
        """注册命令回调（预留）

        Args:
            cmd: 命令字 (int)
            callback: func(payload: bytes) 回调
        """
        self._handlers[cmd] = callback

    def is_connected(self):
        """是否已连接上位机（兼容旧接口）"""
        return self._connected
