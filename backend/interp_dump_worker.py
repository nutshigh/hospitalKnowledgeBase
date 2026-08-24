import signal
import faulthandler

faulthandler.register(signal.SIGUSR1, file=open('/home/wjyy2/logs/interp-dump.log', 'w'), all_threads=True)

from app.modules.interpretation.worker import start_worker

start_worker()
