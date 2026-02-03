## Running the Pi Controller

### Configuration
- Deployment config location: `/etc/polar_feeder/config.json`
- Repo includes a template: `config/config.example.json`
- Runtime config is not committed to version control.

### Logs
- Logs are stored locally on the Pi (CSV). Logs are not committed to version control.

### Run (development or field test)
From the repo root:

```bash
bash src/pi/scripts/run_pi.sh

PC: "log_dir": "logs"


Pi: "log_dir": "/var/log/polar_feeder"
