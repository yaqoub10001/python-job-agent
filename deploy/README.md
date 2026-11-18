# Deploy to an Oracle Always Free Ubuntu VM

Connect to the VM through SSH as its administrator. Use an Ubuntu image with Python 3.11 or newer, such as Ubuntu 24.04. The application itself listens on the VM's loopback interface. Tailscale Serve provides a private HTTPS URL that opens on an Android phone logged into the same Tailscale network. The dashboard has its own password as well.

## Install the app

```sh
sudo apt-get update
sudo apt-get install -y git python3-venv
sudo adduser --system --group --no-create-home job-agent
sudo git clone https://github.com/yaqoub10001/python-job-agent.git /opt/python-job-agent
cd /opt/python-job-agent
sudo python3 -m venv .venv
sudo .venv/bin/python -m pip install -r deploy/requirements.txt
```

Set up the VM as a node in your [Tailscale network](https://tailscale.com/docs/install/linux) and [enable HTTPS for that network](https://tailscale.com/docs/features/https). Use the node's full DNS name from the Tailscale admin console in place of the example below. Enter the dashboard password at the masked prompt and store it somewhere private.

```sh
sudo .venv/bin/python deploy/hosted-env.py your-node.your-network.ts.net
sudo install -m 644 deploy/python-job-agent.service deploy/python-job-discover.service deploy/python-job-discover.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now python-job-agent.service
sudo tailscale serve --bg 8765
```

Open the printed HTTPS address on your phone while it is connected to the same Tailscale network. Sign in as `owner` and upload your résumé. Enable the daily search after you have done that:

```sh
sudo systemctl enable --now python-job-discover.timer
systemctl list-timers python-job-discover.timer
```

The timer runs at 9 AM Pakistan time. It creates drafts for review. It does not send email or submit an employer form. Change the `OnCalendar` time if you prefer another time and reload the systemd configuration.

To send approved direct-email applications, add the SMTP variables from the [main README](../README.md#enable-email-application-sending) using `sudoedit /etc/python-job-agent.env`, then restart `python-job-agent.service`. Keep the environment file owned by root with mode `0600`. Use your provider's TLS submission port and test delivery to an address you control before sending applications.

The dashboard database is at `/var/lib/python-job-agent/workspace.sqlite3`. Back up this file privately using a SQLite-safe backup. The VM, résumé, and application history are independent of the public GitHub repository. View logs with `sudo journalctl -u python-job-agent.service -u python-job-discover.service`.
