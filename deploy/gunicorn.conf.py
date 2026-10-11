bind = "127.0.0.1:8000"
workers = 2
worker_class = "sync"
timeout = 120
max_requests = 1000
max_requests_jitter = 100
preload_app = True

errorlog = "logs/gunicorn-error.log"
accesslog = "logs/gunicorn-access.log"
loglevel = "info"

proc_name = "zqbackend"
