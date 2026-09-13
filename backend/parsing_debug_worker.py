import sys
import threading
import time
import traceback

sys.stdout = open('/home/wjyy2/logs/parsing-debug.log', 'w', buffering=1)


def watcher():
    while True:
        time.sleep(15)
        print("\n===== STACK DUMP =====", flush=True)
        for tid, frame in sys._current_frames().items():
            if tid == threading.main_thread().ident:
                traceback.print_stack(frame, file=sys.stdout)
                print(flush=True)
        print("threads:", threading.active_count(), flush=True)


threading.Thread(target=watcher, daemon=True).start()

from app.core.rabbitmq import rabbitmq
from app.core.database import get_hospital_db
from app.modules.report.service import get_task_status, process_task


def cb(message):
    print('GOT', message.get('_routing_key'), message.get('payload'), flush=True)
    payload = message.get('payload', {})
    hid = payload.get('hospital_id')
    tid = payload.get('task_id')
    print('hospital:', hid, 'task:', tid, flush=True)
    db = next(get_hospital_db(hid))
    print('db ok', flush=True)
    task = get_task_status(db, tid)
    print('task status:', task.status if task else None, flush=True)
    try:
        process_task(db, tid, hid, batch_id=payload.get('batch_id'), file_id=payload.get('file_id'))
        print('PROCESS DONE', flush=True)
    except Exception:
        traceback.print_exc()
        print('PROCESS EXC', flush=True)
    finally:
        db.close()
    print('CB DONE', flush=True)


rabbitmq.consume('parsing.bulk', cb)
print('consuming...', flush=True)
rabbitmq.start_consuming()
