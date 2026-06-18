# tests/test_face_detect.py — host-side AST tests for minimal face_detect baseline.
# Run with:
#   python tests/test_face_detect.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def test_face_detect_has_minimal_run_entry():
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    arg_names = [a.arg for a in run_fn.args.args]
    assert "runtime" in arg_names, "run(runtime) entry is required by reset framework"


def test_face_detect_baseline_has_no_camerai_ui_dependencies():
    """Baseline must stay clean of UI/high-level deps while features are added back.

    Step 3 sanctions `core.face_db` (read-only DB load). Other deps remain
    forbidden until their step is reached.
    """
    src = open(APP_PATH, encoding="utf-8").read()
    forbidden = [
        "core.icon_cache",
        "core.font_manager",
        "ui.theme",
        "scripts._base",
        "BaseScript",
        "_build_ui",
        "_init_db",
        "_do_register",
        "_send_recognition_data",
    ]
    found = [name for name in forbidden if name in src]
    assert not found, "minimal baseline must not include CamerAi business/UI deps: %s" % found


def test_face_detect_loads_face_db_in_main_thread_before_ai():
    """Step 3: face_db.init_features() in run() main thread, BEFORE AI thread start.

    Pitfall #2 variant: file I/O inside the AI thread races with the main
    thread's lv.task_handler() display-DMA flush → deadlock. init_features MUST
    run in run() (main thread), before _thread.start_new_thread(face_det_thread).
    """
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), run_fn) or ""

    assert "from core.face_db import face_db" in src, \
        "run() must import face_db"
    assert "face_db.init_features()" in src, \
        "run() must call face_db.init_features()"

    init_pos = src.find("face_db.init_features()")
    start_thread_pos = src.find("start_new_thread")
    assert 0 <= init_pos < start_thread_pos, \
        "face_db.init_features() must run BEFORE _thread.start_new_thread in run()"

    # And it must NOT be in the AI thread anymore
    ai_fn = _function_node(tree, "face_det_thread")
    ai_src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), ai_fn) or ""
    assert "face_db.init_features()" not in ai_src, \
        "face_db.init_features() must NOT be in face_det_thread (DMA race → deadlock)"


def test_face_detect_baseline_uses_official_two_thread_shape():
    """Baseline should mirror official ai_lvgl.py: face_det_thread + task_handler loop."""
    tree = _parse(APP_PATH)
    _function_node(tree, "face_det_thread")
    run_fn = _function_node(tree, "run")

    has_start_thread = False
    has_task_handler = False
    has_machine_reset = False
    for node in ast.walk(run_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "start_new_thread":
                has_start_thread = True
            if node.func.attr == "task_handler":
                has_task_handler = True
            if node.func.attr == "reset":
                has_machine_reset = True
    assert has_start_thread, "run(runtime) must start face_det_thread via _thread.start_new_thread"
    assert has_task_handler, "run(runtime) main thread must run lv.task_handler()"
    assert not has_machine_reset, "script run() must return; main.py handles reset"


def test_face_det_thread_uses_chn0_chn2_run_show_gc():
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "face_det_thread")
    src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), fn) or ""
    required = [
        "sensor.snapshot(chn=CAM_CHN_ID_0)",
        "sensor.snapshot(chn=CAM_CHN_ID_2)",
        ".to_numpy_ref()",
        ".run(img_np)",
        "Display.show_image",
        "gc.collect()",
    ]
    missing = [text for text in required if text not in src]
    assert not missing, "face_det_thread missing official baseline operations: %s" % missing


def test_face_detection_app_class_exists():
    tree = _parse(APP_PATH)
    _class_node(tree, "FaceDetectionApp")


def test_face_registration_app_class_exists_and_loads_mobile_kmodel():
    """Step 4: FaceRegistrationApp loads mobile kmodel (2.65MB, fits free mem).

    Standard face_recognition.kmodel is 44MB — does NOT fit the ~3.7MB free
    after LVGL double-buffer + face_det load → AIBase.__init__ deadlocks.
    Official main2.py uses standard but has no LVGL (more free mem). CamerAi
    dual-thread LVGL baseline must use mobile (2.65MB, 512-dim features).
    """
    src = open(APP_PATH, encoding="utf-8").read()
    _class_node(_parse(APP_PATH), "FaceRegistrationApp")

    assert "face_recognition_mobile.kmodel" in src, \
        "must load face_recognition_mobile.kmodel (2.65MB fits free mem)"
    # Must NOT use the bare 'face_recognition.kmodel' (44MB, OOM deadlock)
    assert "face_recognition.kmodel\"" not in src and \
           "'face_recognition.kmodel'" not in src, \
        "must NOT load 44MB standard face_recognition.kmodel (OOM deadlock)"


def test_face_reg_loaded_in_main_thread_before_ai():
    """Step 4: face_reg kmodel loaded in run() main thread, before AI thread.

    Pitfall #18: kmodel load is file I/O → must be main thread, before
    _thread.start_new_thread. NOT in face_det_thread.
    """
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), run_fn) or ""

    assert "FaceRegistrationApp(" in src, \
        "run() must construct FaceRegistrationApp (load kmodel)"
    load_pos = src.find("FaceRegistrationApp(")
    start_pos = src.find("_thread.start_new_thread")
    assert 0 <= load_pos < start_pos, \
        "FaceRegistrationApp must be constructed BEFORE _thread.start_new_thread in run()"

    # NOT in AI thread
    ai_fn = _function_node(tree, "face_det_thread")
    ai_src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), ai_fn) or ""
    assert "FaceRegistrationApp(" not in ai_src, \
        "face_reg must NOT be loaded in face_det_thread (pitfall #18)"


def test_face_db_uses_512_dim_features():
    """Step 4: mobile kmodel = 512-dim features → face_db EXPECTED_BYTES=512*4.

    Standard 44MB kmodel (128-dim) OOM-deadlocks under LVGL; mobile (2.65MB,
    512-dim) is the only one that fits → face_db must expect 512-dim.
    """
    import os
    db_path = os.path.join(ROOT, "core", "face_db.py")
    src = open(db_path, encoding="utf-8").read()
    assert "512 * 4" in src or "512*4" in src, \
        "face_db EXPECTED_BYTES must be 512*4 for mobile 512-dim kmodel"
    assert "128 * 4" not in src and "128*4" not in src, \
        "face_db must NOT use 128*4 (that was standard 128-dim, OOM)"


def _load_app_module():
    """Import the app module on host by stubbing board-only deps. Returns module."""
    import importlib.util
    import types
    import sys

    _stubbed = []
    for name in ["lvgl", "image", "nncase_runtime", "ulab", "aidemo", "uctypes",
                 "math", "media", "media.sensor", "media.display", "media.media",
                 "libs", "libs.AIBase", "libs.AI2D", "libs.Utils"]:
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
            _stubbed.append(name)
    np_stub = types.ModuleType("ulab.numpy")
    np_stub.uint8 = None
    sys.modules["ulab.numpy"] = np_stub
    sys.modules["ulab"].numpy = np_stub
    _stubbed.append("ulab.numpy")
    sys.modules["media.sensor"].Sensor = object
    for attr in ("CAM_CHN_ID_0", "CAM_CHN_ID_1", "CAM_CHN_ID_2"):
        setattr(sys.modules["media.sensor"], attr, 0)
    sys.modules["media.display"].Display = object
    sys.modules["media.media"].MediaManager = object
    sys.modules["libs.AIBase"].AIBase = object
    sys.modules["libs.AI2D"].Ai2d = object
    sys.modules["libs.Utils"].ScopedTiming = lambda *a, **k: None
    sys.modules["libs.Utils"].letterbox_pad_param = lambda *a, **k: (0, 0, 0, 0, 0)
    try:
        spec = importlib.util.spec_from_file_location("face_app", APP_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for name in _stubbed:
            sys.modules.pop(name, None)


def test_face_det_postprocess_returns_boxes_and_landms():
    """Step 5: FaceDetectionApp.postprocess returns (boxes, landms) — 2 values.

    Aligns with official main2.py:75-81. Currently baseline drops landms (only
    returns post_ret[0]); umeyama alignment needs landms.
    """
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectionApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "postprocess":
            src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), node) or ""
            # Must return two values (boxes, landms)
            assert "post_ret[0], post_ret[1]" in src or \
                   ("return" in src and src.count("post_ret[") >= 2), \
                "postprocess must return (boxes, landms) — currently drops landms"
            return
    raise AssertionError("postprocess method missing")


def test_face_reg_config_preprocess_implemented():
    """Step 5: FaceRegistrationApp.config_preprocess implemented (umeyama+affine).

    No longer raises NotImplementedError. Must reference umeyama/affine.
    """
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceRegistrationApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "config_preprocess":
            src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), node) or ""
            assert "NotImplementedError" not in src, \
                "config_preprocess must be implemented (no NotImplementedError)"
            assert "_image_umeyama_112" in src or "_get_affine_matrix" in src, \
                "config_preprocess must use umeyama/affine"
            return
    raise AssertionError("config_preprocess method missing")


def test_database_search_zero_face_returns_none():
    """Step 5: database_search returns None when db empty."""
    mod = _load_app_module()
    assert hasattr(mod, "database_search"), "missing database_search function"
    result = mod.database_search(None, {}, 0.75)
    assert result is None, "database_search({}, ...) must return None (0 face)"


def test_face_det_thread_recognizes_largest_face():
    """Step 5: face_det_thread runs face_reg on largest face + database_search."""
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "face_det_thread")
    src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), fn) or ""
    required = [
        "face_reg.run",          # run recognition
        "database_search",       # match against db
        "recognition_results",   # wire to draw_result
        "config_preprocess",     # align before run
    ]
    missing = [t for t in required if t not in src]
    assert not missing, "face_det_thread missing recognition ops: %s" % missing
    # Must select largest face (area-based max)
    assert "max(" in src, "face_det_thread must select largest face via max()"


def test_draw_result_uses_det_index_map():
    """Step 5: draw_result maps recognition_results by det index (largest-face only).

    recognition_results = [(det_index, matched_id), ...]; draw_result builds a
    rec_map {det_index: matched_id} so only the recognized (largest) face colors.
    """
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectionApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "draw_result":
            src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), node) or ""
            assert "rec_map" in src, \
                "draw_result must build rec_map {det_index: matched_id}"
            return
    raise AssertionError("draw_result method missing")


def test_draw_color_uses_abgr_tuple_order():
    """K230 CanMV draw_line/draw_rectangle on Sensor.RGB888 interpret the color
    tuple as (A, B, G, R) — confirmed empirically (green (0,255,0,255) rendered
    magenta because G landed in the B slot). So a hex 0xRRGGBB must map to
    (0xFF, B, G, R). White must stay invariant (channel-order-independent).
    """
    import importlib.util
    import types
    import sys

    # Stub board-only modules so the app module can import on host.
    _stubbed = []
    for name in ["lvgl", "image", "nncase_runtime", "ulab", "aidemo", "uctypes",
                 "media", "media.sensor", "media.display", "media.media",
                 "libs", "libs.AIBase", "libs.AI2D", "libs.Utils"]:
        if name not in sys.modules:
            m = types.ModuleType(name)
            sys.modules[name] = m
            _stubbed.append(name)
    # ulab.numpy is imported as a submodule; register a package-style stub.
    np_stub = types.ModuleType("ulab.numpy")
    np_stub.uint8 = None
    sys.modules["ulab.numpy"] = np_stub
    sys.modules["ulab"].numpy = np_stub
    _stubbed.append("ulab.numpy")
    # Give stubs the attributes the app references at import/class-def time.
    sys.modules["media.sensor"].Sensor = object
    for attr in ("CAM_CHN_ID_0", "CAM_CHN_ID_1", "CAM_CHN_ID_2"):
        setattr(sys.modules["media.sensor"], attr, 0)
    sys.modules["media.display"].Display = object
    sys.modules["media.media"].MediaManager = object
    sys.modules["libs.AIBase"].AIBase = object
    sys.modules["libs.AI2D"].Ai2d = object
    sys.modules["libs.Utils"].ScopedTiming = lambda *a, **k: None
    sys.modules["libs.Utils"].letterbox_pad_param = lambda *a, **k: (0, 0, 0, 0, 0)
    try:
        spec = importlib.util.spec_from_file_location("face_app", APP_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name in _stubbed:
            sys.modules.pop(name, None)

    assert hasattr(mod, "_draw_color"), "missing _draw_color helper"
    # green 0x44CC44: R=0x44,G=0xCC,B=0x44 -> (A=0xFF, B=0x44, G=0xCC, R=0x44)
    assert mod._draw_color(0x44CC44) == (0xFF, 0x44, 0xCC, 0x44)
    # pure red 0xFF0000: R=0xFF -> R-slot (last) must be 0xFF
    assert mod._draw_color(0xFF0000) == (0xFF, 0x00, 0x00, 0xFF)
    # white invariant
    assert mod._draw_color(0xFFFFFF) == (0xFF, 0xFF, 0xFF, 0xFF)


def test_face_det_thread_draws_crosshair():
    """Crosshair must be drawn on img_0 in face_det_thread before draw_result.

    Green crosshair at center (320,240), 2 intersecting lines (vertical + horizontal).
    """
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "face_det_thread")
    src = ast.get_source_segment(open(APP_PATH, encoding="utf-8").read(), fn) or ""

    # Must have crosshair constant
    assert "CROSSHAIR_ARM" in src, "face_det_thread missing CROSSHAIR_ARM"

    # Must have 2 draw_line calls for the crosshair (vertical + horizontal)
    draw_line_count = src.count("draw_line")
    assert draw_line_count >= 2, \
        "face_det_thread needs >=2 draw_line calls for crosshair, got %d" % draw_line_count

    # Crosshair must be drawn BEFORE draw_result (so it appears under detection boxes)
    first_draw_result = src.find("draw_result")
    last_draw_line = src.rfind("draw_line")
    assert last_draw_line < first_draw_result, \
        "crosshair draw_line must come before draw_result in face_det_thread"


def test_face_detect_has_box_colors_and_draw_string():
    """Step 2: detection box + ID labels.

    BOX_COLORS dict (4 IDs), BOX_UNKNOWN white, draw_string_advanced for ID labels.
    """
    src = open(APP_PATH, encoding="utf-8").read()

    assert "BOX_COLORS" in src, "missing BOX_COLORS dict"
    assert "BOX_UNKNOWN" in src, "missing BOX_UNKNOWN constant"
    assert "draw_string_advanced" in src, \
        "missing draw_string_advanced for ID labels"

    # draw_string_advanced must be in draw_result (ID labels on detection boxes)
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectionApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "draw_result":
            dr_src = ast.get_source_segment(src, node) or ""
            assert "draw_string_advanced" in dr_src, \
                "draw_string_advanced must be used in draw_result (ID labels on detection boxes)"
            break
    else:
        raise AssertionError("draw_result method missing from FaceDetectionApp")


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
    if failures:
        print("\n%d FAILED" % failures)
        sys.exit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    test_runner()
