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
import time as _time


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
    TYPE_GESTURE_DETECT = 0x10
    TYPE_BODY_DETECT    = 0x11
    TYPE_OBJECT_CLASSIFY = 0x12
    TYPE_IMAGE_CLASSIFY  = 0x13

    # 单帧 ID 数据最大槽位数(25×10B=250B ≤ length 字段 255 上限)。
    # 与 core/tag_scan.MAX_SLOTS 对齐;tag_detect 全屏扫描动态上报用。
    MAX_ID_SLOTS = 25

    # ── 主机→摄像头 脚本切换命令帧 ──
    # 主机 _camer_changer_camer_mode 发:5A 97 A7 01 FF <mode> <chk> A5(8 字节)
    #   [0]=HEAD [1]=src97 [2]=dstA7(=DEV_ID_CAMER) [3]=len=1
    #   [4]=index=FF(命令) [5]=mode [6]=chk [7]=TAIL
    TYPE_MODE_SWITCH       = 0xFF   # 命令帧 index 字段
    MODE_SWITCH_SRC        = 0x97
    MODE_SWITCH_DST        = 0xA7
    MODE_SWITCH_FRAME_LEN  = 8

    # category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
    CATEGORY_TYPE = {
        "main_menu":  TYPE_MAIN_MENU,     # 0x01
        "settings":   TYPE_MAIN_MENU,     # 0x01（复用主菜单）
        "camera":     TYPE_CAMERA,        # 0x02
        "face_detect":TYPE_FACE_DETECT,   # 0x03
        "tag_detect": TYPE_TAG_DETECT,    # 0x04
        "object_detect": TYPE_OBJECT_DETECT,  # 0x05
        "color_detect": TYPE_COLOR_DETECT,  # 0x06
        "road_detect":  TYPE_ROAD_DETECT,   # 0x07
        "gesture_detect": TYPE_GESTURE_DETECT,  # 0x08
        "body_detect":     TYPE_BODY_DETECT,       # 0x09
        "object_classify": TYPE_OBJECT_CLASSIFY,   # 0x0A
        "image_classify":  TYPE_IMAGE_CLASSIFY,    # 0x13
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }

    # mode → category 反向映射(主机切换命令帧的 mode 字段 → 脚本 category)。
    # 0x01 = 回主菜单(category=None → 清 .next_script + reset)。
    MODE_TO_CATEGORY = {
        0x01: None,               # 主菜单
        0x02: "camera",
        0x03: "face_detect",
        0x04: "tag_detect",
        0x05: "object_detect",
        0x06: "color_detect",
        0x07: "road_detect",
        0x10: "gesture_detect",
        0x11: "body_detect",
        0x12: "object_classify",
        0x13: "image_classify",
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
        # 预分配发送帧缓冲(复用,每帧零分配 → 防主菜单挂机 mem 线性泄漏)。
        # 帧 = HEAD+SRC+DST+length(4) + type(1) + payload(≤250) + chk(1) + TAIL(1)。
        # 数据帧 payload 上限 250(25槽×10B,tag_detect 动态) → 总 257;握手应答 payload 15 → 总 22。
        self._tx = bytearray(257)
        self._tx_len = 0
        # 预分配 id 数据载荷缓冲(250B),send_id_data 每帧复用,零分配。
        self._id_payload = bytearray(250)
        # 板端诊断:低频打印实际 category→msg_type 映射,用于排查主机收到类型不对。
        self._diag_tick_count = 0
        self._diag_last_category = None
        self._diag_last_msg_type = None
        # 主机→摄像头切换命令回调:cb(category),category=str 或 None(回菜单)。
        self._switch_handler = None

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

    @staticmethod
    def _parse_switch_frame(buf, offset):
        """尝试在 buf[offset] 解析主机切换命令帧 8 字节。

        帧: 5A 97 A7 01 FF <mode> <chk> A5
        chk = (0x5A+0x97+0xA7+0x01+0xFF+mode) & 0xFF

        Returns: (mode, next_offset) 或 None(校验失败/长度不足)。
        """
        if offset + 8 > len(buf):
            return None
        if buf[offset]     != 0x5A: return None
        if buf[offset + 1] != 0x97: return None
        if buf[offset + 2] != 0xA7: return None
        if buf[offset + 3] != 0x01: return None
        if buf[offset + 4] != 0xFF: return None
        mode = buf[offset + 5]
        chk = (0x5A + 0x97 + 0xA7 + 0x01 + 0xFF + mode) & 0xFF
        if buf[offset + 6] != chk: return None
        if buf[offset + 7] != 0xA5: return None
        return (mode, offset + 8)

    def send_frame(self, msg_type, payload=b'', length=None):
        """组装并发送完整协议帧(预分配 _tx 复用,每帧零临时分配)。

        Args:
            msg_type: 类型码 (int, 1字节)
            payload: 负载数据 (bytes/bytearray)
            length: 可选实际发送字节数(默认 len(payload);动态载荷用
                    前 N*10 字节切片,避免再分配)
        """
        if length is None:
            length = len(payload)  # 主机 dataAgreeAnalys: data[3]=length 只算 payload,type 在 data[4] 不计入
        tx = self._tx
        # [HEAD, SRC, DST, length, type, *payload, chk, TAIL]
        tx[0] = self.FRAME_HEAD
        tx[1] = self.SRC_ADDR
        tx[2] = self.DST_ADDR
        tx[3] = length
        tx[4] = msg_type
        # 拷贝 payload 到 tx[5:5+length](避免 bytes() 拼接产生临时对象)
        for i in range(length):
            tx[5 + i] = payload[i]
        # 校验和:从 HEAD 到 payload 末尾(不含 chk/TAIL)
        chk = 0
        for i in range(5 + length):
            chk = (chk + tx[i]) & 0xFF
        tx[5 + length] = chk
        tx[5 + length + 1] = self.FRAME_TAIL
        total = 5 + length + 2
        self._tx_len = total
        try:
            self._uart.write(tx[:total])
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
        """发送 N 组 ID 数据(动态数量,泛化 send_face_data,所有脚本共用)。

        Args:
            msg_type: 类型码 (int, 1字节)
            slots: list 或 None。每元素 (id,x,y,w,h,conf)。
                   N = min(len(slots), MAX_ID_SLOTS);None/空 → 0 字节载荷
                   (主菜单/相机"无目标"场景,主机按 length 解析)。
                   每组 10 字节: id(1B) + x(2B BE) + y(2B BE)
                                + w(2B BE) + h(2B BE) + conf(1B)
                   ⚠️ 大端(BE):对齐主机 _camer_cam_data 大端解析
                   (data[off+1]<<8)|data[off+2]。小端会致坐标值错乱。
        """
        buf = self._id_payload  # 预分配复用,每帧零分配
        n = 0
        if slots is not None:
            n = min(len(slots), self.MAX_ID_SLOTS)
        for i in range(n):
            off = i * 10
            fid, x, y, w, h, conf = slots[i]
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
        # 只发前 n*10 字节:length 参数避免切片分配(零分配保持)
        self.send_frame(msg_type, buf, length=n * 10)

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
            if _time.ticks_diff(_time.ticks_ms(), self._last_handshake_ms) < self.HANDSHAKE_COOLDOWN_MS:
                return
        msg_type = self.CATEGORY_TYPE.get(category_id, self.TYPE_MAIN_MENU)
        self._diag_tick_count += 1
        if category_id != self._diag_last_category or msg_type != self._diag_last_msg_type \
                or self._diag_tick_count % 30 == 0:
            print("[HostAPI] tick category=%s msg_type=0x%02X slots=%s" %
                  (category_id, msg_type, "none" if slots is None else "data"))
            self._diag_last_category = category_id
            self._diag_last_msg_type = msg_type
        self.send_id_data(msg_type, slots)

    # ── 握手状态机 ──

    def _read_uart(self):
        """跨固件读取 UART 可用数据(兼容有无 any() 的固件)。

        坑:CanMV K230D v1.2.2 的 machine.UART 无 any()。poll_handshake 原
        依赖 any(),在该固件上每次抛 AttributeError 被 except 吞掉 → 永不读
        数据 → 不应答握手 → 主机收不到任何帧(整机"无法通讯",tick 打印正常
        但协议栈静默死亡)。实测该固件 read() 非阻塞(无数据返回 None),故:
          - 有 any() :读可用字节数(好机器固件路径)
          - 无 any() :直接 read() 轮询,返回 None 即无数据
        """
        try:
            n = self._uart.any()
        except AttributeError:
            # 固件无 any():read() 非阻塞轮询(实测返回 None 当无数据)
            # 用 read(16) 而非 256:握手帧 18B 分两次读完,减少每帧浪费
            try:
                return self._uart.read(16)
            except Exception:
                return None
        except Exception:
            return None
        if n:
            try:
                return self._uart.read(n)
            except Exception:
                return None
        return None

    def poll_handshake(self):
        """非阻塞握手检测：按完整握手请求帧匹配→自动应答。

        应在每帧由 tick() 调用。在 0x5A 对齐边界上匹配完整
        HANDSHAKE_REQUEST_FRAME(参考 DurUI._usart1_try_handshake),不用子串。
        主机超时(没收到数据帧)后会重发握手请求 → 重新应答 + 重置 100ms 计时,
        故入口不短路(_connected 仅作状态标记)。
        """
        # 跨固件兼容读取:有 any() 走 any(),无 any() 走 read() 轮询(坑见 _read_uart)
        raw = self._read_uart()
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
        # 扫描:在每个 0x5A 位置先试握手常量(18B),再试切换命令帧(8B)。
        # 命令帧短(8B)且 index=FF,与握手帧(index=09)不冲突。
        while i < nbuf:
            if buf[i] != self.FRAME_HEAD:
                i += 1
                continue
            # 1) 握手常量匹配(需完整 18B)
            if i + flen <= nbuf and buf[i:i + flen] == frame:
                self._send_handshake_reply()
                buf = bytearray(buf[i + flen:])
                i = 0
                nbuf = len(buf)
                continue
            # 2) 切换命令帧匹配(8B)
            parsed = self._parse_switch_frame(buf, i)
            if parsed is not None:
                mode, _nxt = parsed
                category = self.MODE_TO_CATEGORY.get(mode)
                if mode in self.MODE_TO_CATEGORY:
                    print("[HostAPI] switch frame mode=0x%02X category=%s" %
                          (mode, category))
                    if self._switch_handler is not None:
                        try:
                            self._switch_handler(category)
                        except Exception as e:
                            print("[HostAPI] switch handler error: %s" % e)
                else:
                    print("[HostAPI] switch frame unknown mode=0x%02X" % mode)
                buf = bytearray(buf[i + 8:])
                i = 0
                nbuf = len(buf)
                continue
            # 当前 0x5A 处两种帧都不完整匹配:可能是半帧起点,保留尾部退出
            if i + 8 > nbuf:
                break
            i += 1

        # 保留从最后一个 0x5A 开始的尾部(半帧,等下次拼)
        # MicroPython bytearray.rfind 不接受 int,须传 bytes
        last_head = buf.rfind(bytes([self.FRAME_HEAD]))
        if last_head >= 0:
            self._rx_buf = bytearray(buf[last_head:])
        else:
            self._rx_buf = bytearray()

    def _send_handshake_reply(self):
        """发送握手应答帧 + 记录应答时间戳(100ms 静默期起点)"""
        self.send_frame(self.HANDSHAKE_CMD, self.HANDSHAKE_REPLY_PAYLOAD)
        self._connected = True
        self._last_handshake_ms = _time.ticks_ms()
        print("[HostAPI] handshake reply sent — connected")

    # ── 命令注册（预留）──

    def register_switch_handler(self, callback):
        """注册主机→摄像头切换命令回调。

        Args:
            callback: func(category) — category 为 str(脚本 category_id)
                      或 None(回主菜单)。由 main.py 执行写 .next_script + reset。
        """
        self._switch_handler = callback

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
