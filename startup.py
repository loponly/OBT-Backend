import subprocess
import os
from sys import stdout, platform
from routes.tasks import watchdog
from routes.boot import boot_checks
from multiprocessing import Process
from subprocess import Popen
import time
import signal
import atexit

if __name__ == '__main__':
    boot_checks()
    preargs = []
    webargs = []
    os.environ['OMP_NUM_THREADS'] = '1'
    if platform == 'win32':
        webserver = Popen(["waitress-serve", "server:api", *webargs, "--threads=3"], stdout=stdout, stderr=subprocess.STDOUT)
    else:
        os.system("ulimit -n 2048")
        if os.environ.get('ENVIRONMENT', "dev") == 'prod':
            preargs = ['newrelic-admin', 'run-program']
        if 'PORT' in os.environ:
            webargs.append(f"-b=:{os.environ['PORT']}")
        webserver = Popen([*preargs, "gunicorn", "server:api", *webargs, "--preload", "--threads=4", "--reload"], stdout=stdout, stderr=subprocess.STDOUT)
    tasks = Process(target=watchdog)
    tasks.start()

    @atexit.register
    def clean_up():
        webserver.send_signal(signal.SIGINT)
        time.sleep(0.1)
        webserver.terminate()

    # Exit if gunicorn exited
    while not webserver.poll():
        time.sleep(1)
