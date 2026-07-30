max_requests = 1000
max_requests_jitter = 50
log_file = "-"
bind = "0.0.0.0"
# Long enough for full cost-estimation runs; Azure App Service caps the front-end
# at ~230s, so anything longer than that requires Always On + a higher app-level
# response timeout to actually be useful.
timeout = 600
# Single worker is required because chat_sessions and other workflow state live
# in process memory. Going multi-worker breaks chat sessions on the second
# message (the request can land on a different worker that doesn't know the id).
# If you ever need horizontal scale, move chat_sessions to SQLite/Redis first.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
