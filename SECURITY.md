# Security

NSW is a local desktop application. It opens no network connections, runs no
server, and reads only the files and folders the user selects.

## Data protection built into the application

- Measurement files are opened read-only; exports into a configured data folder are
  refused.
- Personal paths belong in `config/local.yaml`, which is git-ignored.
- `.gitignore` excludes instrument file types, databases, logs and the `local/` folder
  so that measurements are not committed by accident.

## Reporting a problem

Please report a vulnerability, or private data found in this repository, through
GitHub's private vulnerability reporting ("Report a vulnerability" on the Security
tab) rather than a public issue. There is no formal support commitment at the alpha
stage.
