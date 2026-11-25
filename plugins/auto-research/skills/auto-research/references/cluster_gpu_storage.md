# Multi-Node GPU Scheduling, Standby Reserve SOP & Cross-Node Storage Reference

Always verify current node identity first (`hostname`) and use `scripts/cluster_ctl.py` (which reads `config/cluster.local.json`, or falls back to single-node auto-discovery) rather than hand-crafting password SSH commands.

---

## 1. Configuring Cluster Topology (`config/cluster.local.json`)

Copy `config/cluster.example.json` to `config/cluster.local.json` (gitignored) to register your SSH nodes (`node1..nodeN`), candidate local data disks (`/mnt/data*`), optional shared NAS paths (`production_root`, `frozen_readonly_paths`), and optional standby GPU daemon (`gpu_auto_reserve`).

---

## 2. Standby GPU Claim & Return SOP (`RESERVED_BY_US`)

When a node runs a 1-second standby GPU reservation daemon (`gpu_auto_reserve` in `cluster.local.json`), idle GPUs are held in `reserved_by_us` (~23.8 GiB / 100% util in raw `nvidia-smi`). Follow this 4-step SOP strictly:

1. **Inspect ownership first**:
   ```bash
   python3 "${AUTO_RESEARCH_HOME:-$HOME/auto_research}/scripts/cluster_ctl.py" status
   ```
   Confirm target `<ID>` is marked `IDLE` or `RESERVED_BY_US`. **Never touch GPUs marked `EXCLUDED_IN_USE` (active production or parallel sessions).**
2. **Atomically release & exempt before launching** (mandatory for `RESERVED_BY_US`; never use `stop` or `kill` and never omit `--gpus`):
   ```bash
   <control.sh> release --gpus <ID>
   ```
   Keep `<ID>` exempted throughout all rounds of a multi-experiment Auto Research loop (do **not** toggle `watch` between iterations).
3. **Bind physical GPU IDs explicitly**:
   - Direct local command: `export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<ID>`
   - Via `cluster_ctl.py`: `--node <node> --gpus <ID>` (automatically exports both variables and fails fast if `<ID>` is still held in `reserved_by_us`).
4. **Return GPU only when ALL experiment rounds finish**:
   ```bash
   <control.sh> watch --gpus <ID>
   ```
   (**Never** run `control.sh watch --all` or `control.sh stop`!)

---

## 3. Standard Cross-Node GPU Workflow (`cluster_ctl.py`)

```bash
HUB="${AUTO_RESEARCH_HOME:-$HOME/auto_research}"

# 1. Probe live GPUs & disk/inode availability across all configured nodes
python3 "$HUB/scripts/cluster_ctl.py" status
python3 "$HUB/scripts/cluster_ctl.py" recommend-gpus --count 1 --min-free-gb 50

# 2. Check target node Python/venv & sync lightweight experiment code (excluding heavy runs/ & data/)
python3 "$HUB/scripts/cluster_ctl.py" exec --node node3 \
  --cmd 'which python3; ls -d ~/.venv* /usr/local/anaconda3/bin/python3 2>/dev/null || true'

python3 "$HUB/scripts/cluster_ctl.py" sync \
  --src "$HUB/projects/my_exp/" \
  --dst "node3:/mnt/data/$USER/auto_research_runs/my_exp/" \
  --exclude runs --exclude data --exclude .git

# 3. Launch detached GPU job & monitor via `schedule` + `check-job` (no sleep loops!)
python3 "$HUB/scripts/cluster_ctl.py" launch-bg \
  --node node3 --gpus 0 \
  --workdir "/mnt/data/$USER/auto_research_runs/my_exp" \
  --log "/mnt/data/$USER/auto_research_runs/my_exp/runs/exp01/train.log" \
  --pid-file "/mnt/data/$USER/auto_research_runs/my_exp/runs/exp01/train.pid" \
  --cmd 'python3 src/train.py --config configs/exp01.yaml'

python3 "$HUB/scripts/cluster_ctl.py" check-job \
  --node node3 --pid <PID> \
  --log "/mnt/data/$USER/auto_research_runs/my_exp/runs/exp01/train.log" --tail 50
```

---

## 4. Storage & Data Transfer Strategy

1. **Shared NAS**: Read shared dataset manifests directly where mounted; never mutate paths listed in `frozen_readonly_paths`; avoid high-IOPS temporary checkpoint writes on NAS.
2. **Node-Local High-Speed Data Disks (`/mnt/data*`)**: Store features, caches, and training checkpoints under `<local_data_disk>/$USER/<exp_name>/` after verifying `avail_gb` and `inode_pct` via `cluster_ctl.py status`.
3. **Pulling Metrics & Winning Checkpoints Back (`cluster_ctl.py sync`)**: Verify integrity before removing temporary replicas; never delete a unique copy first.
