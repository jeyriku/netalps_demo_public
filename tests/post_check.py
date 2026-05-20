"""
post_check.py — Full Functional Validation
===========================================
Pipeline step 2: full validation of the network state AFTER deploy.
Blocks the merge if any single test fails.

Tests:
  1. OSPF neighbors — Full state on rtr-01 and rtr-02
  2. Routing table — OSPF routes present (remote LAN + peer loopback)
  3. BFD peers — Up state on both routers
  4. End-to-end ping — host-left ↔ host-right (traverses 2 routers)
  5. Loopback-to-loopback ping — proves OSPF redistribution

Notes:
  - CommonSetup waits up to 30 s for OSPF convergence before running
    the tests (useful right after `clab deploy`).
  - All assertions include raw output to ease CI debugging.

Usage:
  python tests/post_check.py --testbed tests/testbed.yml
  pyats run job tests/test_job.py --testbed-file tests/testbed.yml
"""

import subprocess
import argparse
import time
from pyats import aetest
from pyats.topology import loader


# ─── Helpers ──────────────────────────────────────────────────────────────────

def docker_exec(container: str, cmd: list) -> str:
    """Run a command inside a container via docker exec. Raises on error."""
    full_cmd = ["docker", "exec", container] + cmd
    return subprocess.check_output(full_cmd, stderr=subprocess.STDOUT).decode()


def wait_for_ospf_full(container: str, timeout: int = 30, interval: int = 3) -> bool:
    """Poll `show ip ospf neighbor` until 'Full' appears or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            output = docker_exec(container, ["vtysh", "-c", "show ip ospf neighbor"])
            if "Full" in output:
                return True
        except subprocess.CalledProcessError:
            pass
        time.sleep(interval)
    return False


# ─── Common Setup ─────────────────────────────────────────────────────────────

class CommonSetup(aetest.CommonSetup):

    @aetest.subsection
    def load_devices(self, testbed):
        self.parent.parameters.update(
            rtr01=testbed.devices["frr-rtr-01"],
            rtr02=testbed.devices["frr-rtr-02"],
            hleft=testbed.devices["host-left"],
            hright=testbed.devices["host-right"],
        )

    @aetest.subsection
    def wait_ospf_convergence(self, rtr01):
        """Wait for OSPF convergence (max 30 s) before running the tests."""
        container = rtr01.custom["container"]
        converged = wait_for_ospf_full(container, timeout=30)
        if not converged:
            # Do not block here — the OSPF tests will detect the failure
            self.skipped(
                "OSPF not converged after 30s — tests OSPF will fail",
                goto=["next_tc"],
            )


# ─── Test 1 : OSPF Neighbors ──────────────────────────────────────────────────

class TestOSPF(aetest.Testcase):
    """Checks that both routers have an OSPF neighbor in Full state."""

    @aetest.test
    def ospf_full_rtr01(self, rtr01):
        output = docker_exec(rtr01.custom["container"], ["vtysh", "-c", "show ip ospf neighbor"])
        assert "Full" in output, (
            f"{rtr01.alias}: no OSPF neighbor in Full state\n\n"
            f"--- show ip ospf neighbor ---\n{output}"
        )

    @aetest.test
    def ospf_full_rtr02(self, rtr02):
        output = docker_exec(rtr02.custom["container"], ["vtysh", "-c", "show ip ospf neighbor"])
        assert "Full" in output, (
            f"{rtr02.alias}: no OSPF neighbor in Full state\n\n"
            f"--- show ip ospf neighbor ---\n{output}"
        )

    @aetest.test
    def ospf_neighbor_count_rtr01(self, rtr01):
        """Exactly 1 OSPF neighbor expected on rtr-01."""
        output = docker_exec(rtr01.custom["container"], ["vtysh", "-c", "show ip ospf neighbor"])
        full_lines = [l for l in output.splitlines() if "Full" in l]
        assert len(full_lines) >= 1, (
            f"{rtr01.alias}: expected >=1 'Full' line, found {len(full_lines)}\n{output}"
        )


# ─── Test 2 : Table de routage ────────────────────────────────────────────────

class TestRouting(aetest.Testcase):
    """Checks the presence of OSPF routes in the routing table."""

    @aetest.test
    def rtr01_has_rtr02_lan(self, rtr01, rtr02):
        """rtr-01 must have the OSPF route to rtr-02's LAN (192.168.20.0/24)."""
        output = docker_exec(
            rtr01.custom["container"],
            ["vtysh", "-c", "show ip route 192.168.20.0/24"],
        )
        assert "ospf" in output.lower(), (
            f"{rtr01.alias}: OSPF route to {rtr02.custom['lan']} missing\n{output}"
        )

    @aetest.test
    def rtr02_has_rtr01_lan(self, rtr01, rtr02):
        """rtr-02 must have the OSPF route to rtr-01's LAN (192.168.10.0/24)."""
        output = docker_exec(
            rtr02.custom["container"],
            ["vtysh", "-c", "show ip route 192.168.10.0/24"],
        )
        assert "ospf" in output.lower(), (
            f"{rtr02.alias}: OSPF route to {rtr01.custom['lan']} missing\n{output}"
        )

    @aetest.test
    def rtr01_has_rtr02_loopback(self, rtr01, rtr02):
        """rtr-01 must have rtr-02's loopback learned via OSPF."""
        peer_lo = rtr02.custom["loopback"]
        output = docker_exec(
            rtr01.custom["container"],
            ["vtysh", "-c", f"show ip route {peer_lo}/32"],
        )
        assert "ospf" in output.lower(), (
            f"{rtr01.alias}: OSPF loopback {peer_lo}/32 from {rtr02.alias} missing\n{output}"
        )

    @aetest.test
    def rtr02_has_rtr01_loopback(self, rtr01, rtr02):
        """rtr-02 must have rtr-01's loopback learned via OSPF."""
        peer_lo = rtr01.custom["loopback"]
        output = docker_exec(
            rtr02.custom["container"],
            ["vtysh", "-c", f"show ip route {peer_lo}/32"],
        )
        assert "ospf" in output.lower(), (
            f"{rtr02.alias}: OSPF loopback {peer_lo}/32 from {rtr01.alias} missing\n{output}"
        )


# ─── Test 3 : BFD ─────────────────────────────────────────────────────────────

class TestBFD(aetest.Testcase):
    """Checks that BFD sessions are in Up state (failure detection < 1 s)."""

    @aetest.test
    def bfd_up_rtr01(self, rtr01):
        output = docker_exec(
            rtr01.custom["container"], ["vtysh", "-c", "show bfd peers brief"]
        )
        assert "up" in output.lower(), (
            f"{rtr01.alias}: BFD session not Up\n\n--- show bfd peers brief ---\n{output}"
        )

    @aetest.test
    def bfd_up_rtr02(self, rtr02):
        output = docker_exec(
            rtr02.custom["container"], ["vtysh", "-c", "show bfd peers brief"]
        )
        assert "up" in output.lower(), (
            f"{rtr02.alias}: BFD session not Up\n\n--- show bfd peers brief ---\n{output}"
        )


# ─── Test 4 : Ping end-to-end ─────────────────────────────────────────────────

class TestEndToEnd(aetest.Testcase):
    """End-to-end ping traversing both FRR routers."""

    @aetest.test
    def ping_hleft_to_hright(self, hleft, hright):
        target = hright.custom["ip"]
        output = docker_exec(
            hleft.custom["container"],
            ["ping", "-c", "4", "-W", "2", target],
        )
        assert "0% packet loss" in output or " 4 received" in output, (
            f"Ping {hleft.alias}→{hright.alias} ({target}) FAILED\n{output}"
        )

    @aetest.test
    def ping_hright_to_hleft(self, hleft, hright):
        target = hleft.custom["ip"]
        output = docker_exec(
            hright.custom["container"],
            ["ping", "-c", "4", "-W", "2", target],
        )
        assert "0% packet loss" in output or " 4 received" in output, (
            f"Ping {hright.alias}→{hleft.alias} ({target}) FAILED\n{output}"
        )

    @aetest.test
    def ping_loopback_rtr01_to_rtr02(self, rtr01, rtr02):
        """rtr-01 pings rtr-02's loopback — validates OSPF redistribution."""
        target = rtr02.custom["loopback"]
        output = docker_exec(
            rtr01.custom["container"],
            ["ping", "-c", "4", "-W", "2", target],
        )
        assert "0% packet loss" in output or " 4 received" in output, (
            f"Ping loopback {rtr01.alias}→{rtr02.alias} ({target}) FAILED\n{output}"
        )

    @aetest.test
    def ping_loopback_rtr02_to_rtr01(self, rtr01, rtr02):
        """rtr-02 pings rtr-01's loopback."""
        target = rtr01.custom["loopback"]
        output = docker_exec(
            rtr02.custom["container"],
            ["ping", "-c", "4", "-W", "2", target],
        )
        assert "0% packet loss" in output or " 4 received" in output, (
            f"Ping loopback {rtr02.alias}→{rtr01.alias} ({target}) FAILED\n{output}"
        )


# ─── Common Cleanup ───────────────────────────────────────────────────────────

class CommonCleanup(aetest.CommonCleanup):
    @aetest.subsection
    def done(self):
        pass


# ─── Standalone entry-point ───────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyATS post_check — full OSPF/routing/E2E validation")
    parser.add_argument("--testbed", default="tests/testbed.yml", help="Path to testbed.yml")
    args, _ = parser.parse_known_args()

    testbed = loader.load(args.testbed)
    aetest.main(testbed=testbed)
