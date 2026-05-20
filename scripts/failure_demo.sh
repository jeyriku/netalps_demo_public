#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# failure_demo.sh — CI Failure Scenario Demo
# ─────────────────────────────────────────────────────────────────────────────
# Replays a full CI/CD pipeline with injection of a broken FRR config:
#
#   Step 1 : Deploy the lab (normal topology)
#   Step 2 : pre_check (wait for containers + daemons OK)
#   Step 3 : post_check baseline → expected PASS (nominal initial state)
#   Step 4 : Backup the good config and inject a broken config on frr-rtr-02
#   Step 5 : post_check → expected FAIL (OSPF adjacency/routes broken)
#   Step 6 : Restore the original config and prove recovery
#   Step 7 : Destroy the lab
#
# Prerequisites:
#   - Containerlab installed
#   - Python venv activated (pyats)
#   - Be in the repository folder or set DEMO_DIR
#
# Usage:
#   cd /path/to/repository
#   source /path/to/venv/bin/activate
#   bash scripts/failure_demo.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DEMO_DIR="${DEMO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
TOPO="$DEMO_DIR/topology.clab.yml"
TESTBED="$DEMO_DIR/tests/testbed.yml"
CONTAINER_RTR02="clab-frr-cicd-demo-frr-rtr-02"
BACKUP_CONF="$(mktemp /tmp/frr-rtr-02-backup.XXXXXX.conf)"
BAD_CONF="$DEMO_DIR/tests/failure_scenario/frr-rtr-02/frr.conf"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

step()  { echo -e "\n${CYAN}══ $* ${NC}"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }

# ── Cleanup on EXIT ───────────────────────────────────────────────────────────
cleanup() {
    echo ""
    warn "Interruption detected — destroying the lab..."
    containerlab destroy -t "$TOPO" --cleanup 2>/dev/null || true
    rm -f "$BACKUP_CONF" 2>/dev/null || true
}
trap cleanup INT TERM

# ── Step 1 : Deploy ───────────────────────────────────────────────────────────
step "1/7 — Deploy the lab"
cd "$DEMO_DIR"
containerlab deploy -t "$TOPO" --reconfigure
info "Waiting 20 s for initial OSPF convergence..."
sleep 20
ok "Lab deployed"

# ── Step 2 : pre_check ────────────────────────────────────────────────────────
step "2/7 — Pre-check (infrastructure sanity)"
cd "$DEMO_DIR"
python tests/pre_check.py --testbed "$TESTBED" && ok "pre_check PASSED" || {
    fail "pre_check FAILED — aborting"
    exit 1
}

# ── Step 3 : initial post_check (baseline / everything OK) ────────────────────
step "3/7 — Post-check baseline (expected: PASSED)"
python tests/post_check.py --testbed "$TESTBED" && ok "Baseline PASSED" || {
    fail "Baseline FAILED — check lab before continuing"
    exit 1
}

# ── Step 4 : Inject broken config ─────────────────────────────────────────────
step "4/7 — Inject broken config on frr-rtr-02 (OSPF area 1 instead of 0)"
info "Saving current config to $BACKUP_CONF"
docker cp "$CONTAINER_RTR02:/etc/frr/frr.conf" "$BACKUP_CONF"
warn "Applying the broken OSPF config to frr-rtr-02"
docker cp "$BAD_CONF" "$CONTAINER_RTR02:/etc/frr/frr.conf"
docker exec "$CONTAINER_RTR02" vtysh -b 2>/dev/null || true
info "Waiting 15 s for OSPF adjacency to drop..."
sleep 15
warn "Broken config injected — area mismatch active"

# ── Step 5 : post_check on broken config (expected: FAILED) ───────────────────
step "5/7 — Post-check on broken config (expected: FAILED)"
set +e
python tests/post_check.py --testbed "$TESTBED"
EXIT_CODE=$?
set -e

if [[ $EXIT_CODE -ne 0 ]]; then
    ok "Test FAILED as expected (exit $EXIT_CODE) — CI/CD pipeline blocked ✓"
    info "Observed impact on the routers:"
    docker exec "clab-frr-cicd-demo-frr-rtr-01" vtysh -c "show ip ospf neighbor" || true
    docker exec "clab-frr-cicd-demo-frr-rtr-02" vtysh -c "show ip ospf neighbor" || true
    docker exec "clab-frr-cicd-demo-frr-rtr-01" vtysh -c "show ip route 192.168.20.0/24" || true
    docker exec "clab-frr-cicd-demo-frr-rtr-02" vtysh -c "show ip route 192.168.10.0/24" || true
else
    warn "Test PASSED but a failure was expected — check the injected config"
fi

# ── Step 6 : Rollback to original config ──────────────────────────────────────
step "6/7 — Rollback to original config"
docker cp "$BACKUP_CONF" "$CONTAINER_RTR02:/etc/frr/frr.conf"
docker exec "$CONTAINER_RTR02" vtysh -b 2>/dev/null || true
info "Waiting 15 s for OSPF re-convergence..."
sleep 15

python tests/post_check.py --testbed "$TESTBED" && ok "Post-rollback PASSED — service restored ✓" || {
    fail "Post-rollback FAILED — rollback did not restore service"
    exit 1
}

# ── Step 7 : Destroy ──────────────────────────────────────────────────────────
step "7/7 — Destroy the lab"
trap - INT TERM
containerlab destroy -t "$TOPO" --cleanup
rm -f "$BACKUP_CONF" 2>/dev/null || true
ok "Lab destroyed"

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Failure demo completed successfully${NC}"
echo -e "${GREEN}  CI/CD pipeline blocked on broken config → rollback OK${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════════${NC}"
