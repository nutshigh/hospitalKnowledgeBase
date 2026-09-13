import signal
import faulthandler
import sys

faulthandler.register(signal.SIGUSR1, file=open('/home/wjyy2/logs/thread-dump.log', 'w'), all_threads=True)

from app.core.rabbitmq import rabbitmq
from app.core.database import get_hospital_db
from app.modules.report.service import get_task_status, process_task


def cb(message):
    payload = message.get('payload', {})
    hid = payload.get('hospital_id')
    tid = payload.get('task_id')
    db = next(get_hospital_db(hid))
    try:
        process_task(db, tid, hid, batch_id=payload.get('batch_id'), file_id=payload.get('file_id'))
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        db.close()


rabbitmq.consume('parsing.bulk', cb)
print('consuming...', flush=True)
rabbitmq.start_consuming()
