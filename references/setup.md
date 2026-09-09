# First use

Python 3.9+ on macOS/Linux/WSL, no dependencies. Commands below run from the
repository root against a disposable example directory, never an existing project.

```sh
mkdir demo-project
python3 scripts/artifacts.py begin --project demo-project --task demo --owner example --reason 'Disposable tutorial output'
```

Write a sample file inside the returned payload directory. Then:

```sh
python3 scripts/artifacts.py finish --project demo-project --task demo --owner example --keep-days 7
python3 scripts/artifacts.py sweep --project demo-project
python3 scripts/artifacts.py restore --project demo-project --task demo --owner example --destination recovered-demo
```

The preview deletes nothing. Restore requires an absent destination and pins the
original. `--keep-days` accepts 0–3650; zero requires `--apply` for deletion.
Choose retention and authorization before wiring an actual scheduler.

Example project instruction to customize and approve:

> Put your disposable helper files in the managed store. At task closure retain
> them for 14 days. Do not sweep historical groups during ordinary work. Scheduled
> sweeps may remove completed, unchanged, unpinned expired groups in this project.
> Never put source, customer data or unique deliverables in disposable groups.

For scheduling, give your existing scheduler an absolute Python executable,
absolute script path, explicit project path, and `sweep` initially. Check its output;
add `--apply` only after the policy permits it. The schedule itself must be enabled
and verified on the user's host. No default account, timezone or automation ID exists.
