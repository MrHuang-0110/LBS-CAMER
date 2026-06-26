# core/icon_cache.py — 脚本图标缓存
#
# K230 硬约束：lv.task_handler() 运行后，SD/FATFS 文件读取
# 会与显示 flush 抢占共享资源导致死锁或渲染异常。
# 因此所有脚本图标必须在 main() 首次 task_handler 之前预读到内存。
#
# 本模块在启动阶段预读，脚本运行时直接从缓存取数据，零文件 I/O。

import lvgl as lv


class _IconCache:
    """全局图标缓存 — 启动阶段预读，运行阶段只读"""

    def __init__(self):
        self._settings_icons = {}  # item_id → (data, dsc)
        self._back_icon = None     # (data, dsc) or None
        self._camera_icons = {}   # name → (data, dsc)
        self._face_icons = {}     # name → (data, dsc)
        self._tag_icons = {}      # name → (data, dsc)
        self._object_icons = {}   # name → (data, dsc)
        self._color_icons = {}      # name → (data, dsc)

    def preload_settings_icons(self):
        """预读设置页图标（在首次 task_handler 之前调用）"""
        icons = {
            "language": "/sdcard/CamerAi/resource/icons/settings_icon/Language.png",
            "about":    "/sdcard/CamerAi/resource/icons/settings_icon/about.png",
        }
        for item_id, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._settings_icons[item_id] = (data, dsc)
                print(f"[IconCache] settings/{item_id} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] settings/{item_id} FAILED: {e}")

        # 同时预读返回按钮图标
        self.preload_back_icon()

    def preload_back_icon(self):
        """独立预读返回按钮图标（供 init_app 调用，脚本顶栏返回钮需要）。

        _back_icon 原本只在 preload_settings_icons()（仅 init_menu 调）里预读，
        走 init_app 的脚本（模板/settings）顶栏返回钮拿不到图标。本方法独立
        预读，供 init_app 调用。幂等：已预读则跳过。
        """
        if self._back_icon is not None:
            return
        back_path = "/sdcard/CamerAi/resource/icons/settings_icon/back.png"
        try:
            with open(back_path, 'rb') as f:
                data = bytearray(f.read())
            dsc = lv.img_dsc_t({
                'data_size': len(data),
                'data': data,
            })
            self._back_icon = (data, dsc)
            print(f"[IconCache] back OK ({len(data)} bytes)")
        except Exception as e:
            print(f"[IconCache] back FAILED: {e}")

    def get_settings_icon(self, item_id):
        """获取设置页图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._settings_icons.get(item_id, (None, None))

    def get_back_icon(self):
        """获取返回按钮图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._back_icon or (None, None)

    def preload_camera_icons(self):
        """预读相机 APP 图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/camera_icon/"
        icons = {
            "back":    base + "back.png",
            "shutter": base + "photo.png",
            "gallery": base + "camera.png",
            "mode":    base + "countdown.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._camera_icons[name] = (data, dsc)
                print(f"[IconCache] camera/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] camera/{name} FAILED: {e}")

    def get_camera_icon(self, name):
        """获取相机图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._camera_icons.get(name, (None, None))

    def preload_face_icons(self):
        """预读人脸识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/face_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._face_icons[name] = (data, dsc)
                print(f"[IconCache] face/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] face/{name} FAILED: {e}")

    def get_face_icon(self, name):
        """获取人脸识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._face_icons.get(name, (None, None))

    def preload_tag_icons(self):
        """预读标签识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/tag_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._tag_icons[name] = (data, dsc)
                print(f"[IconCache] tag/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] tag/{name} FAILED: {e}")

    def get_tag_icon(self, name):
        """获取标签识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._tag_icons.get(name, (None, None))

    def preload_object_icons(self):
        """预读物体识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAI/resource/icons/object_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._object_icons[name] = (data, dsc)
                print(f"[IconCache] object/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] object/{name} FAILED: {e}")

    def get_object_icon(self, name):
        """获取物体识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._object_icons.get(name, (None, None))

    def preload_color_icons(self):
        """预读颜色识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/color_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._color_icons[name] = (data, dsc)
                print(f"[IconCache] color/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] color/{name} FAILED: {e}")

    def get_color_icon(self, name):
        """获取颜色识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._color_icons.get(name, (None, None))


# 全局单例
icon_cache = _IconCache()
