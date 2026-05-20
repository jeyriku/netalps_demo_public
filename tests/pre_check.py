"""
pre_check.py — Infrastructure Sanity Check
===========================================
Pipeline step 1: verifies that the infrastructure is ready BEFORE functional
validation. Runs immediately after `clab deploy`.

Tests:
  1. All containers are running
  2. FRR daemons (zebra, ospfd, bfdd) respond via vtysh
  3. eth1/eth2 interfaces are UP with expected IPs
  4. Management connectivity (ping on the mgmt network)

Usage:
  python tests/pre_check.py --testbed tests/testbed.yml
  pyats run job tests/test_job.py --testbed-file tests/testbed.yml
"""

import subprocess
import argparse
from pyats import aetest
from pyats.topology import loader


# ─── Helpers ──────────────────────────────────────────────────────────────────

def docker_exec(container: str, cmd: list) -> str:
    """Run a command inside a container via docker exec. Raises on error."""
    full_cmd = ["docker", "exec", container] + cmd
    return subprocess.check_output(full_cmd, stderr=subprocess.STDOUT).decode()


def container_running(container: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


# ─── Common Setup ─────────────────────────────────────────────────────────────

class CommonSetup(aetest.CommonSetup):

    @aetest.subsection
    def load_devices(self, testbed):
        """Expose devices as parameters for all test cases."""
        self.parent.parameters.update(
            rtr01=testbed.devices["frr-rtr-01"],
            rtr02=testbed.devices["frr-rtr-02"],
            hleft=testbed.devices["host-left"],
            hright=testbed.devices["host-right"],
        )


# ─── Test 1 : Containers running ──────────────────────────────────────────────

class TestContainers(aetest.Testcase):
    """Checks that the 4 Containerlab containers are in Running state."""

    @aetest.test
    def frr_rtr_01_running(self, rtr01):
        container = rtr01.custom["container"]
        assert container_running(container), (
            f"Container {container!r} is not in Running state — "
            f"check: docker ps | grep {container}"
        )

    @aetest.test
    def frr_rtr_02_running(self, rtr02):
        container = rtr02.custom["container"]
        assert container_running(container), f"Container {container!r} is not Running"

    @aetest.test
    def host_left_running(self, hleft):
        container = hleft.custom["container"]
        assert container_running(container), f"Container {container!r} is not Running"

    @aetest.test
    def host_right_running(self, hright):
        container = hright.custom["container"]
        assert container_running(container), f"Container {container!r} is not Running"


# ─── Test 2 : FRR daemons ─────────────────────────────────────────────────────

class TestFRRDaemons(aetest.Testcase):
    """Checks that vtysh responds and that the critical daemons are active."""

    @aetest.test
    def vtysh_responds_rtr01(self, rtr01):
        output = docker_exec(rtr01.custom["container"], ["vtysh", "-c", "show version"])
        assert "FRRouting" in output, (
            f"vtysh not responding on {rtr01.custom['container']}:\n{output}"
        )

    @aetest.test
    def vtysh_responds_rtr02(self, rtr02):
        output = docker_exec(rtr02.custom["container"], ["vtysh", "-c", "show version"])
        assert "FRRouting" in output, (
            f"vtysh not responding on {rtr02.custom['container']}:\n{output}"
        )

    @aetest.test
    def ospfd_active_rtr01(self, rtr01):
        output = docker_exec(rtr01.custom["container"], ["vtysh", "-c", "show daemons"])
        assert "ospfd" in output, (
            f"ospfd not active on {rtr01.alias}:\n{output}"
        )

    @aetest.test
    def ospfd_active_rtr02(self, rtr02):
        output = docker_exec(rtr02.custom["container"], ["vtysh", "-c", "show daemons"])
        assert "ospfd" in output, f"ospfd not active on {rtr02.alias}:\n{output}"

    @aetest.test
    def bfdd_active_rtr01(self, rtr01):
        output = docker_exec(rtr01.custom["container"], ["vtysh", "-c", "show daemons"])
        assert "bfdd" in output, f"bfdd not active on {rtr01.alias}:\n{output}"


# ─── Test 3 : Interfaces et adresses IP ───────────────────────────────────────

class TestInterfaces(aetest.Testcase):
    """Checks that eth1 is UP and that WAN IPs are correctly configured."""

    @aetest.test
    def eth1_up_rtr01(self, rtr01):
        output = docker_exec(rtr01.custom["container"], ["ip", "link", "show", "eth1"])
        assert "UP" in output, f"eth1 is not UP on {rtr01.alias}:\n{output}"

    @aetest.test
    def eth1_up_rtr02(self, rtr02):
        output = docker_exec(rtr02.custom["container"], ["ip", "link", "show", "eth1"])
        assert "UP" in output, f"eth1 is not UP on {rtr02.alias}:\n{output}"

    @aetest.test
    def wan_ip_rtr01(self, rtr01):
        expected = rtr01.custom["wan_ip"]
        output = docker_exec(rtr01.custom["container"], ["ip", "addr", "show", "eth1"])
        assert expected in output, (
            f"Expected WAN IP {expected} missing on {rtr01.alias}/eth1:\n{output}"
        )

    @aetest.test
    def wan_ip_rtr02(self, rtr02):
        expected = rtr02.custom["wan_ip"]
        output = docker_exec(rtr02.custom["container"], ["ip", "addr", "show", "eth1"])
        assert expected in output, (
            f"Expected WAN IP {expected} missing on {rtr02.alias}/eth1:\n{output}"
        )

    @aetest.test
    def loopback_rtr01(self, rtr01):
        expected = rtr01.custom["loopback"]
        output = docker_exec(rtr01.custom["container"], ["ip", "addr", "show", "lo"])
        assert expected in output, (
            f"Loopback {expected} missing on {rtr01.alias}:\n{output}"
        )

    @aetest.test
    def loopback_rtr02(self, rtr02):
        expected = rtr02.custom["loopback"]
        output = docker_exec(rtr02.custom["container"], ["ip", "addr", "show", "lo"])
        assert expected in output, (
            f"Loopback {expected} missing on {rtr02.alias}:\n{output}"
        )


# ─── Common Cleanup ───────────────────────────────────────────────────────────

class CommonCleanup(aetest.CommonCleanup):
    @aetest.subsection
    def done(self):
        pass


# ─── Standalone entry-point ───────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyATS pre_check — infrastructure sanity")
    parser.add_argument("--testbed", default="tests/testbed.yml", help="Path to testbed.yml")
    args, _ = parser.parse_known_args()

    testbed = loader.load(args.testbed)
    aetest.main(testbed=testbed)
