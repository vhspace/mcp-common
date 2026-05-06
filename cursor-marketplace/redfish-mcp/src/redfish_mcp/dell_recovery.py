"""Dell iDRAC recovery via Serial Over LAN (SOL) and GRUB manipulation.

Automates the process of booting a Dell server with custom kernel parameters
via iDRAC SOL + GRUB command-line mode. Designed for scenarios where the OS
is unreachable (e.g., a systemd service crashes the NIC) and the fix must be
applied through the boot process itself.

The approach:
  1. Ensure BIOS serial console redirection is configured for SOL
  2. Ensure the disk boot device is first (disable PXE if needed)
  3. Power cycle and connect to iDRAC SOL via racadm SSH
  4. Catch GRUB menu, enter command-line mode ('c')
  5. Boot with custom kernel params (e.g., systemd.mask=, systemd.run=)
  6. Verify health after the self-healing boot cycle

Requires: pexpect, sshpass (on the host running this code)
Dell-specific: uses racadm CLI, iDRAC SOL, Dell BIOS attribute names
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

logger = logging.getLogger("redfish_mcp.dell_recovery")


@dataclass
class RecoveryResult:
    ok: bool
    host: str
    steps: list[dict[str, str]] = field(default_factory=list)
    health_before: str | None = None
    health_after: str | None = None
    error: str | None = None

    def log(self, step: str, status: str, detail: str = "") -> None:
        self.steps.append({"step": step, "status": status, "detail": detail})
        logger.info("recovery %s: %s %s %s", self.host, step, status, detail)

    def to_dict(self) -> dict:
        d: dict = {"ok": self.ok, "host": self.host, "steps": self.steps}
        if self.health_before:
            d["health_before"] = self.health_before
        if self.health_after:
            d["health_after"] = self.health_after
        if self.error:
            d["error"] = self.error
        return d


def _check_prerequisites() -> str | None:
    """Return error message if prerequisites are missing, else None."""
    if not shutil.which("sshpass"):
        return "sshpass not found in PATH (required for iDRAC SSH authentication)"
    try:
        import pexpect  # noqa: F401
    except ImportError:
        return "pexpect not installed (pip install pexpect)"
    return None


def _redfish_get_health(host: str, user: str, password: str, verify_tls: bool) -> str | None:
    """Quick Redfish health check."""
    flag = "-sk" if not verify_tls else "-s"
    try:
        r = subprocess.run(
            [
                "curl",
                flag,
                "-u",
                f"{user}:{password}",
                f"https://{host}/redfish/v1/Systems/System.Embedded.1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(r.stdout)
        return data.get("Status", {}).get("Health")
    except Exception:
        return None


def _racadm_ssh(
    host: str, user: str, password: str, command: str, timeout: int = 20
) -> tuple[int, str]:
    """Execute a racadm command via SSH to the iDRAC."""
    result = subprocess.run(
        [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            f"ConnectTimeout={min(timeout, 15)}",
            "-tt",
            f"{user}@{host}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout + result.stderr


def _ensure_serial_console(host: str, user: str, password: str) -> tuple[bool, str]:
    """Ensure BIOS serial console redirection is enabled for SOL.

    Dell R670 iDRAC10 requires:
      SerialComm = OnConRedir
      SerialPortAddress = Com2

    Returns (needs_reboot, message).
    """
    rc, out = _racadm_ssh(host, user, password, "racadm get BIOS.SerialCommSettings")
    if rc != 0:
        return False, f"Failed to query serial settings: {out}"

    needs_change = False
    if "SerialComm=OnConRedir" not in out:
        _racadm_ssh(
            host, user, password, "racadm set BIOS.SerialCommSettings.SerialComm OnConRedir"
        )
        needs_change = True

    if "SerialPortAddress=Com2" not in out:
        _racadm_ssh(
            host, user, password, "racadm set BIOS.SerialCommSettings.SerialPortAddress Com2"
        )
        needs_change = True

    if needs_change:
        _racadm_ssh(host, user, password, "racadm jobqueue create BIOS.Setup.1-1 -r forced")
        return True, "Serial console configured, BIOS job created with reboot"

    return False, "Serial console already configured"


def _ensure_disk_boot(host: str, user: str, password: str) -> tuple[bool, str]:
    """Ensure disk boots before PXE (disable PXE if needed).

    Returns (needs_reboot, message).
    """
    _rc, out = _racadm_ssh(host, user, password, "racadm get BIOS.NetworkSettings.PxeDev1EnDis")
    if "PxeDev1EnDis=Enabled" in out:
        _racadm_ssh(host, user, password, "racadm set BIOS.NetworkSettings.PxeDev1EnDis Disabled")
        _racadm_ssh(host, user, password, "racadm jobqueue create BIOS.Setup.1-1 -r forced")
        return True, "PXE disabled to force disk boot, BIOS job created with reboot"

    return False, "PXE already disabled or disk-first boot order"


def _wait_for_bios_jobs(host: str, user: str, password: str, timeout_s: int = 600) -> bool:
    """Wait for all pending BIOS jobs to complete."""
    start = time.time()
    while time.time() - start < timeout_s:
        _rc, out = _racadm_ssh(host, user, password, "racadm jobqueue view")
        if "Status=Scheduled" not in out and "Status=Running" not in out:
            return True
        time.sleep(30)
    return False


def _redfish_power(host: str, user: str, password: str, action: str, verify_tls: bool) -> bool:
    """Send Redfish power action (On, ForceOff, ForceRestart)."""
    flag = "-sk" if not verify_tls else "-s"
    r = subprocess.run(
        [
            "curl",
            flag,
            "-u",
            f"{user}:{password}",
            "-X",
            "POST",
            f"https://{host}/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"ResetType": action}),
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return r.stdout.strip() in ("200", "204")


def _sol_grub_boot(
    host: str,
    user: str,
    password: str,
    linux_cmd: str,
    initrd_cmd: str,
    verify_tls: bool,
    grub_timeout_s: int = 360,
) -> tuple[bool, str]:
    """Connect to SOL, catch GRUB, boot with custom kernel cmdline.

    1. Power off, connect SOL, power on
    2. Wait for GRUB
    3. Press 'c' for command-line
    4. Type linux, initrd, boot commands
    5. Disconnect

    Returns (success, message).
    """
    import pexpect

    _redfish_power(host, user, password, "ForceOff", verify_tls)
    time.sleep(15)

    ssh_cmd = (
        f"sshpass -p '{password}' ssh -o StrictHostKeyChecking=no "
        f"-o PubkeyAuthentication=no -tt {user}@{host} 'console com2'"
    )
    child = pexpect.spawn(
        "/bin/bash",
        ["-c", ssh_cmd],
        encoding="latin-1",
        timeout=grub_timeout_s + 120,
    )

    try:
        child.expect("Connected", timeout=30)
    except pexpect.TIMEOUT:
        child.close()
        return False, "Failed to connect to iDRAC SOL"

    _redfish_power(host, user, password, "On", verify_tls)

    try:
        child.expect("GNU GRUB", timeout=grub_timeout_s)
    except pexpect.TIMEOUT:
        child.close()
        return False, f"GRUB not detected within {grub_timeout_s}s (PXE may still be enabled)"

    time.sleep(1)
    child.send("c")
    time.sleep(3)

    for ch in linux_cmd:
        child.send(ch)
        time.sleep(0.03)
    time.sleep(1)
    child.sendline("")
    time.sleep(2)

    for ch in initrd_cmd:
        child.send(ch)
        time.sleep(0.03)
    time.sleep(1)
    child.sendline("")
    time.sleep(2)

    for ch in "boot":
        child.send(ch)
        time.sleep(0.03)
    child.sendline("")

    time.sleep(3)
    child.send("\x1c")  # Ctrl-\ to disconnect SOL
    time.sleep(1)
    child.close()

    return True, "GRUB boot command sent successfully"


def run_dell_grub_recovery(
    *,
    host: str,
    user: str,
    password: str,
    service_name: str,
    kernel_version: str,
    root_uuid: str,
    additional_kernel_params: str = "pci=realloc=off",
    verify_tls: bool = False,
    boot_wait_s: int = 300,
    re_enable_pxe: bool = True,
) -> RecoveryResult:
    """Execute a full Dell iDRAC GRUB recovery cycle.

    Boots the server with systemd.mask=<service> to prevent the service from
    running, plus systemd.run="systemctl disable <service>" to permanently
    disable it, plus systemd.run_success_action=reboot to auto-reboot.

    After the self-healing boot, verifies BMC health is OK.

    Args:
        host: iDRAC/BMC IP address
        user: BMC username
        password: BMC password
        service_name: systemd service to disable (e.g., "disable_acs.service")
        kernel_version: Linux kernel version (e.g., "6.8.0-101-generic")
        root_uuid: Root filesystem UUID
        additional_kernel_params: Extra kernel params to append
        verify_tls: Whether to verify TLS certificates
        boot_wait_s: Seconds to wait for the full boot+reboot cycle
        re_enable_pxe: Whether to re-enable PXE boot after recovery
    """
    result = RecoveryResult(ok=False, host=host)

    prereq_err = _check_prerequisites()
    if prereq_err:
        result.error = prereq_err
        result.log("prerequisites", "FAIL", prereq_err)
        return result

    # Check health before
    result.health_before = _redfish_get_health(host, user, password, verify_tls)
    result.log("health_check", "INFO", f"health_before={result.health_before}")

    # Step 1: Ensure serial console
    result.log("serial_console", "START", "Checking BIOS serial console settings")
    needs_reboot, msg = _ensure_serial_console(host, user, password)
    result.log("serial_console", "OK" if not needs_reboot else "CHANGED", msg)

    if needs_reboot:
        result.log("wait_bios_job", "START", "Waiting for serial console BIOS job")
        if not _wait_for_bios_jobs(host, user, password, timeout_s=600):
            result.error = "BIOS job for serial console timed out"
            result.log("wait_bios_job", "FAIL", result.error)
            return result
        result.log("wait_bios_job", "OK", "BIOS job completed")

    # Step 2: Ensure disk boot (disable PXE)
    result.log("boot_order", "START", "Checking boot order")
    needs_reboot, msg = _ensure_disk_boot(host, user, password)
    result.log("boot_order", "OK" if not needs_reboot else "CHANGED", msg)

    if needs_reboot:
        result.log("wait_bios_job", "START", "Waiting for boot order BIOS job")
        if not _wait_for_bios_jobs(host, user, password, timeout_s=600):
            result.error = "BIOS job for boot order timed out"
            result.log("wait_bios_job", "FAIL", result.error)
            return result
        result.log("wait_bios_job", "OK", "BIOS job completed")

    # Step 3: Build kernel command lines
    extra = f" {additional_kernel_params}".rstrip() if additional_kernel_params else ""
    linux_cmd = (
        f"linux /boot/vmlinuz-{kernel_version} "
        f"root=UUID={root_uuid} ro "
        f"systemd.mask={service_name} "
        f'systemd.run="/bin/systemctl disable {service_name}" '
        f"systemd.run_success_action=reboot"
        f"{extra}"
    )
    initrd_cmd = f"initrd /boot/initrd.img-{kernel_version}"

    result.log("grub_boot", "START", f"Booting with mask={service_name}, auto-disable, auto-reboot")

    # Step 4: SOL + GRUB boot
    ok, msg = _sol_grub_boot(
        host=host,
        user=user,
        password=password,
        linux_cmd=linux_cmd,
        initrd_cmd=initrd_cmd,
        verify_tls=verify_tls,
    )
    if not ok:
        result.error = msg
        result.log("grub_boot", "FAIL", msg)
        return result
    result.log("grub_boot", "OK", msg)

    # Step 5: Wait for self-healing boot cycle (boot -> disable -> reboot)
    result.log("wait_reboot", "START", f"Waiting {boot_wait_s}s for boot+reboot cycle")
    time.sleep(boot_wait_s)
    result.log("wait_reboot", "OK", "Wait complete")

    # Step 6: Verify health
    result.health_after = _redfish_get_health(host, user, password, verify_tls)
    result.log("health_check", "DONE", f"health_after={result.health_after}")

    if result.health_after == "OK":
        result.ok = True
        result.log("result", "SUCCESS", f"Service {service_name} permanently disabled, health OK")
    else:
        result.error = f"Health is {result.health_after} after recovery (expected OK)"
        result.log("result", "FAIL", result.error)

    # Step 7: Re-enable PXE if requested
    if re_enable_pxe:
        result.log("restore_pxe", "START", "Re-enabling PXE boot")
        _racadm_ssh(host, user, password, "racadm set BIOS.NetworkSettings.PxeDev1EnDis Enabled")
        _racadm_ssh(host, user, password, "racadm jobqueue create BIOS.Setup.1-1 -r forced")
        result.log("restore_pxe", "OK", "PXE re-enabled (will apply on next reboot)")

    return result
