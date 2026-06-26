# tests/test_face_detect_template.py — host-side contracts for face_detect Phase 1.
# Run with:
#   python tests/test_face_detect_template.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")


def _src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse(path=APP_PATH):
    return ast.parse(_src(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _module_function_names(tree):
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _called_names(fn_node):
    names = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_face_detect_has_template_entry_and_on_frame():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    on_frame_fn = _function_node(tree, "on_frame")
    assert "runtime" in [a.arg for a in run_fn.args.args], "run(runtime) required"
    assert "img" in [a.arg for a in on_frame_fn.args.args], "on_frame(img) required"


def test_face_detect_imports_face_detection_app_from_core():
    src = _src()
    assert "from core.face_ai import" in src, "must import from core.face_ai"
    assert "FaceDetectionApp" in src, "must use FaceDetectionApp"


def test_face_detect_has_no_threads_or_self_media_init():
    src = _src()
    forbidden = [
        "import _thread", "start_new_thread", "face_det_thread",
        "def media_init", "def lvgl_init", "def disp_drv_flush_cb",
        "Display.init", "MediaManager.init", "sensor.run",
    ]
    found = [token for token in forbidden if token in src]
    assert not found, "Phase 1 template app must not self-init media or start threads: %s" % found


def test_run_uses_chn0_preview_and_task_handler():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "snapshot(chn=CAM_CHN_ID_0)" in src, "run() must snapshot chn0 preview"
    assert "on_frame(img)" in src, "run() must call on_frame(img) before show_image"
    assert "Display.show_image" in src, "run() must show chn0 preview on OSD1"
    assert "lv.task_handler()" in src, "run() must service LVGL"
    assert src.find("on_frame(img)") < src.find("Display.show_image"), \
        "run loop order must be on_frame before Display.show_image"


def test_on_frame_uses_chn2_and_does_not_show_image():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "snapshot(chn=CAM_CHN_ID_2)" in src, "on_frame must snapshot chn2 NPU input"
    assert ".to_numpy_ref()" in src, "on_frame must convert chn2 frame to numpy ref"
    assert ".run(img_np)" in src or ".run(" in src, "on_frame must run detector"
    assert ".draw_result(img" in src, "on_frame must draw boxes onto chn0 preview img"
    assert "Display.show_image" not in src, "on_frame must not write display directly"


def test_on_frame_draws_center_green_crosshair():
    """on_frame 须在屏幕居中画一个小绿色十字(对齐 tag_detect)。

    VGA 640x480 屏幕中心 (320, 240)。draw_cross(x, y, color, size, thickness)。
    """
    fn = _function_node(_parse(), "on_frame")
    seg = ast.get_source_segment(_src(), fn) or ""
    assert "draw_cross" in seg, "on_frame must draw a center crosshair"
    assert "320" in seg and "240" in seg, "crosshair must be at screen center (320, 240)"


def test_ai_init_and_deinit_helpers_exist_and_used():
    tree = _parse()
    funcs = _module_function_names(tree)
    assert "_init_ai" in funcs, "must initialize anchors/kmodel before main loop"
    assert "_deinit_ai" in funcs, "must clean AI after loop"
    run_fn = _function_node(tree, "run")
    called = _called_names(run_fn)
    assert "_init_ai" in called, "run() must call _init_ai"
    assert "_deinit_ai" in called, "run() must call _deinit_ai"


def test_file_io_paths_are_in_init_not_on_frame():
    tree = _parse()
    init_fn = _function_node(tree, "_init_ai")
    on_frame_fn = _function_node(tree, "on_frame")
    init_src = ast.get_source_segment(_src(), init_fn) or ""
    on_frame_src = ast.get_source_segment(_src(), on_frame_fn) or ""
    assert "prior_data_320.bin" in init_src, "_init_ai must load anchors before loop"
    assert "face_detection_320.kmodel" in init_src, "_init_ai must load kmodel before loop"
    assert "prior_data_320.bin" not in on_frame_src, "on_frame must not load anchors"
    assert "face_detection_320.kmodel" not in on_frame_src, "on_frame must not load kmodel"
    assert "open(" not in on_frame_src and "fromfile" not in on_frame_src, \
        "on_frame must not perform file I/O"


def test_on_frame_collects_gc_after_npu_run():
    """K230 NPU buffers accumulate without per-frame GC and face_det.run hangs.

    Board evidence: Phase1 single-thread reached frame47 and stopped after
    to_numpy_ref, before after-face_det.run log. No LVGL objects are created or
    deleted in on_frame, so a post-inference gc.collect is the minimal safe fix.
    """
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "gc.collect()" in src, "on_frame must collect GC after NPU run/draw"


def test_template_ui_helpers_exist():
    tree = _parse()
    funcs = _module_function_names(tree)
    for name in ("_build_ui", "_destroy_ui"):
        assert name in funcs, "template UI helper missing: %s" % name
    src = _src()
    assert "category.face_detect" in src, "title must use lang.t('category.face_detect')"
    assert "set_style_bg_opa(0" in src, "screen/preview must be transparent for OSD1"


def test_face_detect_imports_recognition_assets():
    src = _src()
    assert "FaceRegistrationApp" in src, "must import FaceRegistrationApp"
    assert "face_db" in src, "must use face_db"
    assert "id_registry" in src, "must use id_registry"
    assert "database_search" in src, "must use database_search"


def test_run_loads_face_reg_kmodel_before_loop():
    """Both kmodels must be loaded before the main loop (pitfall #18).

    face_reg construction lives in _init_ai (see test_face_reg_built_before_det_preprocess);
    run() calls _init_ai before the loop, so the reg kmodel path appears in the whole app
    source, constructed before 'while not exit_flag'.
    """
    src = _src()
    assert "face_recognition_mobile.kmodel" in src, "must load mobile reg kmodel"
    assert "FaceRegistrationApp(" in src, "must construct FaceRegistrationApp"
    assert src.find("FaceRegistrationApp(") < src.find("while not exit_flag"), \
        "face_reg must be constructed before the main loop (pitfall #18)"


def test_face_reg_built_before_det_preprocess():
    """face_reg kmodel must load BEFORE face_det.config_preprocess().

    Board root cause (dual-kmodel hang): loading the second kmodel (face_reg) AFTER
    face_det.config_preprocess() builds face_det's AI2D corrupts the shared NPU/AI2D
    state → frame1 hang + black screen, even with per-frame gc. Both kmodels must be
    loaded first, then config_preprocess. Aligns with old Step4 revision.

    Checked via AST inside _init_ai (excludes docstrings) so the order of actual
    statements is what's verified, not comment text.
    """
    tree = _parse()
    init_fn = _function_node(tree, "_init_ai")
    reg_line = None
    pre_line = None
    for node in ast.walk(init_fn):
        if isinstance(node, ast.Call):
            # FaceRegistrationApp(...) construction
            if isinstance(node.func, ast.Name) and node.func.id == "FaceRegistrationApp":
                reg_line = node.lineno
            # <something>.config_preprocess() call
            if isinstance(node.func, ast.Attribute) and node.func.attr == "config_preprocess":
                pre_line = node.lineno
    assert reg_line is not None, "_init_ai must construct FaceRegistrationApp"
    assert pre_line is not None, "_init_ai must call config_preprocess"
    assert reg_line < pre_line, \
        "FaceRegistrationApp must be constructed BEFORE face_det.config_preprocess() " \
        "(dual-kmodel NPU state corruption → frame1 hang)"


def test_run_main_loop_polls_k2():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "poll_k2()" in src, "main loop must call id_registry.poll_k2()"


def test_on_frame_recognizes_largest_face():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "database_search" in src, "on_frame must match largest face via database_search"
    assert "face_reg.run" in src or "face_reg" in src, "on_frame must extract feature via face_reg"


def test_on_frame_no_runtime_disk_io():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    for token in ("flush_to_disk", "os.remove", "open("):
        assert token not in src, "on_frame must not do disk I/O (pitfall #2): %s" % token


def test_run_persists_at_exit():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "flush_to_disk" in src or "face_db.flush_to_disk" in src, \
        "run() exit must persist face_db (flush or clear)"


def test_overlay_close_is_deferred():
    """Clear/Save button callbacks must not delete overlay (use-after-free)."""
    src = _src()
    assert "_process_overlay_close" in src, "must have deferred overlay-close handler"
    assert "_close_overlay" in src, "must use a close flag, not direct delete in callback"


def test_clear_save_callbacks_no_disk_io():
    src = _src()
    # The clear handler must set clear_dirty + flag, not os.remove directly.
    assert "face_db.clear()" in src or ".clear()" in src, "clear button must call face_db.clear()"
    assert "os.remove" not in src, "app.py must not call os.remove directly (deferred to face_db)"


def test_bottom_bar_has_list_icon_and_overlay():
    src = _src()
    assert "list" in src, "bottom bar must have list icon trigger"
    assert "清除" in src and "保存" in src, "overlay must have Clear/Save buttons"


def test_title_is_recognition():
    """标题用 i18n(category.face_detect),跟随语言切换,不硬编码中文。

    旧:TITLE_TEXT="人脸识别" 硬编码 → settings 切语言后 face_detect 标题不变。
    新:_build_ui 用 runtime.lang.t("category.face_detect")。
    """
    src = _src()
    assert 'lang.t("category.face_detect")' in src or "lang.t('category.face_detect')" in src, \
        "title must use runtime.lang.t('category.face_detect') for i18n, not hardcoded"
    # 不得硬编码标题字符串字面量(TITLE_TEXT 常量赋值中文)
    assert 'TITLE_TEXT = "人脸识别"' not in src and "TITLE_TEXT = '人脸识别'" not in src, \
        "must not hardcode TITLE_TEXT as Chinese string; use lang.t"


def test_face_detect_back_icon_uses_face_icon_set():
    """顶栏返回图标用 face_detect_icon/back.png(get_face_icon('back')),
    不用 settings 的 get_back_icon()。

    用户要求 face_detect 返回图标用 face_detect_icon/back.png。
    """
    src = _src()
    assert 'get_face_icon("back")' in src or "get_face_icon('back')" in src, \
        "top bar back icon must use icon_cache.get_face_icon('back') (face_detect_icon/back.png)"
    assert "get_back_icon()" not in src, \
        "face_detect must not use get_back_icon() (settings icon); use get_face_icon('back')"


def test_face_detect_on_frame_recognizes_all_faces():
    """on_frame 必须对每个检测框跑 reg（识全部脸），不再只取 max_i。

    断言 on_frame 源码含遍历 det_boxes 的 reg 循环 + database_search。
    """
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    # 识全部脸：遍历检测框跑 reg（不再只 max_i 单次）
    assert "for i in range(len(det_boxes))" in src or \
           "for i, " in src, \
        "on_frame must loop over all det_boxes to run reg per face"
    assert "database_search" in src, "on_frame must call database_search per face"
    assert "config_preprocess(landms" in src, \
        "on_frame must config_preprocess per-face landmarks"


def test_face_detect_on_frame_builds_four_slots():
    """on_frame 必须构建4槽位 list（slots[mid-1]=...）并调 host_tick(slots)。"""
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "slots = [None, None, None, None]" in src, \
        "on_frame must init 4-slot list"
    assert "slots[mid - 1]" in src or "slots[mid-1]" in src, \
        "on_frame must fill slot by matched id (slots[mid-1])"
    assert "host_tick(slots)" in src, \
        "on_frame must call host_tick(slots)"


def test_face_detect_on_frame_still_supports_k2_register():
    """on_frame 仍保留 K2 注册逻辑（最大脸注册），has_pending + try_register。"""
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "has_pending" in src, "on_frame must keep K2 has_pending check"
    assert "try_register" in src, "on_frame must keep try_register"


def test_face_detect_on_frame_confidence_uses_match_score_not_det():
    """on_frame 置信度必须用 database_search 返回的匹配度 score,不得用 det[4]。

    根因:置信度 = face_reg 特征与 DB 特征的余弦匹配度(需自己算),检测框 det
    不含匹配度。旧代码 conf = int(det[4]*100) 误用检测框第5元素 → 一直0。
    database_search 返回 (mid, score),conf = int(score*100)。
    """
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    # database_search 返回值解包出 score,conf 用 score 算
    assert "score" in src, "on_frame must unpack score from database_search"
    assert "score * 100" in src or "score*100" in src, \
        "conf must be int(score*100) from match score"
    assert "det[4]" not in src, \
        "on_frame must NOT use det[4] as confidence (det has no match score)"


def test_runner():
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn) and name != "test_runner"]
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
