#!/usr/bin/env bash
set -euo pipefail

python3 <<'PY'
from __future__ import annotations

import os
import plistlib
import subprocess
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk


DOMAIN = f"gui/{os.getuid()}"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


@dataclass(frozen=True)
class Service:
    label: str
    path: Path
    state: str


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def notification(message: str) -> None:
    run(
        [
            "osascript",
            "-e",
            f'display notification "{message.replace(chr(34), chr(39))}" with title "Launchctl Service Control"',
        ]
    )


def service_state(label: str) -> str:
    result = run(["launchctl", "print", f"{DOMAIN}/{label}"])
    if result.returncode != 0:
        return "Stopped"
    if "\tstate = running" in result.stdout:
        return "Running"
    return "Loaded"


def read_services() -> list[Service]:
    services: list[Service] = []
    if not LAUNCH_AGENTS_DIR.exists():
        return services
    for path in sorted(LAUNCH_AGENTS_DIR.glob("*.plist")):
        try:
            with path.open("rb") as handle:
                label = plistlib.load(handle).get("Label", "")
        except Exception:
            label = ""
        if isinstance(label, str) and label:
            services.append(Service(label=label, path=path, state=service_state(label)))
    return services


def stop_service(service: Service) -> None:
    run(["launchctl", "bootout", DOMAIN, str(service.path)])
    notification(f"Stopped {service.label}")


def start_service(service: Service) -> None:
    run(["launchctl", "bootstrap", DOMAIN, str(service.path)])
    run(["launchctl", "enable", f"{DOMAIN}/{service.label}"])
    run(["launchctl", "kickstart", "-k", f"{DOMAIN}/{service.label}"])
    notification(f"Started {service.label}")


def restart_service(service: Service) -> None:
    run(["launchctl", "bootout", DOMAIN, str(service.path)])
    start_service(service)
    notification(f"Restarted {service.label}")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Launchctl Service Control")
        self.geometry("920x420")
        self.minsize(760, 320)
        self.services: list[Service] = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        title = ttk.Label(
            self,
            text="Local LaunchAgent Services",
            font=("Helvetica", 16, "bold"),
        )
        title.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))

        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        columns = ("state", "label", "plist")
        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("state", text="State")
        self.tree.heading("label", text="Label")
        self.tree.heading("plist", text="Plist")
        self.tree.column("state", width=90, stretch=False, anchor="center")
        self.tree.column("label", width=260, stretch=False)
        self.tree.column("plist", width=560, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        button_bar = ttk.Frame(self)
        button_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        button_bar.columnconfigure(0, weight=1)

        ttk.Button(button_bar, text="Refresh", command=self.refresh).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(button_bar, text="Start", command=lambda: self.act("start")).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(button_bar, text="Stop", command=lambda: self.act("stop")).grid(
            row=0, column=3, padx=4
        )
        ttk.Button(button_bar, text="Restart", command=lambda: self.act("restart")).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(button_bar, text="Close", command=self.destroy).grid(
            row=0, column=5, padx=(8, 0)
        )

        self.refresh()

    def refresh(self) -> None:
        self.services = read_services()
        self.tree.delete(*self.tree.get_children())
        for index, service in enumerate(self.services):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(service.state, service.label, str(service.path)),
            )

    def selected_service(self) -> Service | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(
                "Launchctl Service Control",
                "Select a service first.",
            )
            return None
        return self.services[int(selected[0])]

    def act(self, action: str) -> None:
        service = self.selected_service()
        if service is None:
            return
        try:
            if action == "start":
                start_service(service)
            elif action == "stop":
                stop_service(service)
            elif action == "restart":
                restart_service(service)
            else:
                raise ValueError(action)
        except Exception as exc:
            messagebox.showerror("Launchctl Service Control", str(exc))
            return
        self.refresh()


if __name__ == "__main__":
    App().mainloop()
PY
