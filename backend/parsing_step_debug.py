import sys
import time
import threading

sys.stdout = open('/home/wjyy2/logs/parsing-step.log', 'w', buffering=1)


def watcher():
    while True:
        time.sleep(20)
        print("\n===== STACK DUMP =====", flush=True)
        import traceback
        for tid, frame in sys._current_frames().items():
            if tid == threading.main_thread().ident:
                traceback.print_stack(frame, file=sys.stdout)
        print("threads:", threading.active_count(), flush=True)


threading.Thread(target=watcher, daemon=True).start()

from app.core.database import get_hospital_db
from app.modules.report.service import get_task_status, _pdf_has_text, _extract_pdf_text, _parse_text_with_llm

print("start", flush=True)
db = next(get_hospital_db("H004"))
task = get_task_status(db, 4)
print("task:", task.id, task.file_type, task.original_file_path, flush=True)
path = task.original_file_path
print("step1: _pdf_has_text", flush=True)
t0 = time.time()
has = _pdf_has_text(path)
print(f"step1 done: {has} ({time.time()-t0:.1f}s)", flush=True)
print("step2: _extract_pdf_text", flush=True)
t0 = time.time()
text = _extract_pdf_text(path)
print(f"step2 done: len={len(text)} ({time.time()-t0:.1f}s)", flush=True)
print("step3: _parse_text_with_llm", flush=True)
t0 = time.time()
parsed = _parse_text_with_llm(text)
print(f"step3 done: keys={list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)} ({time.time()-t0:.1f}s)", flush=True)
db.close()
print("ALL DONE", flush=True)
