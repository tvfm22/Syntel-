# Deployment material

Current production deployment uses Docker and Liara:

- `../../Dockerfile`
- `../../entrypoint.sh`
- `../../liara.json`
- `../gunicorn_config.py`

`baremetal/` contains the older nginx/systemd deployment scripts. It is kept
for reference and is not used by the Docker image.

`legacy-config/` contains obsolete nested `.dockerignore`/`.gitignore` files
from the old two-level project layout.
