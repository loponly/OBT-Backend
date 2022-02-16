# Troubleshooting guide
## SQLite3 Unable to open database
You've likely hit the file-descriptor limit \
Check it with `ulimit -n` on linux, it should be >1024 at least