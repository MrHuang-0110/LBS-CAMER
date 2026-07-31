# tools/uart_rx_probe.py — UART1 双向环回探针(独立调试工具,不依赖 main.py/协议栈)
#
# 用途:把「UART1 硬件 + FPIOA 引脚 + 接线/转接 + 波特率」从完整固件里剥离出来,
#       单独验证 K230 与主机的物理链路是否通。不跑 main.py,不走握手协议。
#
# 用法:
#   1. CanMV IDE 打开本文件 → 先中断板子上正在跑的程序 → 运行本脚本。
#   2. 主机串口工具(SSCOM/xshell 等,115200 8N1)连主机侧 USB 转串口,
#      手动发送任意字节(如 "Please Link" 或 55 AA 01 02 03)。
#   3. 看板端打印:应出现 [probe] RX nB: <hex>。
#   4. 同时看主机工具:应收到 K230 原样回显的相同字节(验证 TX 方向)。
#
# 判定:
#   - 出现 [probe] RX + 主机收到回显 → UART1 双向链路全通,问题在固件/协议侧
#   - 有 [probe] ready 但无 RX 打印           → K230 RX 没收到数据(线/转接/主机工具)
#   - 连 [probe] ready 都没有                  → IDE/板子连接或运行方式问题
#
# 注意:本文件仅调试用,不属于 CamerAi 固件;定位完成后可删除。

from machine import FPIOA, UART
import time

# 与 core/app_runtime.py:_init_services 完全一致的 UART1 引脚配置
fpioa = FPIOA()
fpioa.set_function(40, FPIOA.UART1_TXD)
fpioa.set_function(41, FPIOA.UART1_RXD)

uart = UART(UART.UART1, baudrate=115200,
            bits=UART.EIGHTBITS, parity=UART.PARITY_NONE,
            stop=UART.STOPBITS_ONE)

print("[probe] UART1 ready (GPIO40=TX GPIO41=RX, 115200 8N1), waiting for RX...")
# 打印 UART 实例可用方法——排查固件 API 差异(坏机器实测无 any(),此列表是修复依据)
print("[probe] UART methods:", sorted(m for m in dir(uart) if not m.startswith("_")))

# 探测 read() 是否非阻塞:无数据时 read(1) 应立即返回 None(不卡)。
# 若此行卡住不返回 = read 阻塞 → 修复不能用 read 轮询,需另定方案。
t0 = time.ticks_ms()
probe_read = uart.read(1)
dt = time.ticks_diff(time.ticks_ms(), t0)
print("[probe] read(1) no-data -> %r, elapsed=%dms (None+fast=非阻塞, 可轮询)" % (probe_read, dt))

last_beat = time.ticks_ms()
while True:
    # 不用 any():坏机器固件(CanMV K230D v1.2.2)的 UART 无 any(),实测
    # read() 非阻塞(无数据返回 None),直接 read() 轮询即可跨固件工作。
    try:
        raw = uart.read(256)
    except Exception as e:
        print("[probe] read() error: %s" % e)
        break
    if raw:
        hexs = ''.join('%02x' % raw[i] for i in range(len(raw)))
        print("[probe] RX %dB: %s" % (len(raw), hexs))
        # 原样回显到 TX:主机工具应收回相同字节(验证发送方向)
        try:
            uart.write(raw)
            print("[probe] echo TX %dB" % len(raw))
        except Exception as e:
            print("[probe] write() error: %s" % e)
    # 心跳:每 2s 打一次,区分"在跑但没收到" vs "探针没跑起来"
    if time.ticks_diff(time.ticks_ms(), last_beat) >= 2000:
        last_beat = time.ticks_ms()
        print("[probe] waiting... no RX in last 2s")
    time.sleep_ms(10)
