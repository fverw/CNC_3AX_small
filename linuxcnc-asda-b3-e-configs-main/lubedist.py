#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lubedist - wegabhaengige Schmierung (z.B. MQL) fuer LinuxCNC

Zaehlt den zurueckgelegten Weg (Betrag von delta-Position) und gibt nach
jeweils <interval> Einheiten einen Schmierimpuls von <pulse-time> Sekunden aus.
Der Wegzaehler wird in einer Datei gespeichert und beim naechsten Start wieder
geladen, d.h. er ueberlebt Ausschalten und Neustart.

Zwei Betriebsarten (--mode):
    separate (Default)  jede Achse hat eigenen Zaehler, Intervall und Ausgang
    sum                 die Wege ALLER Achsen werden zu EINEM Zaehler addiert
                        (Summe der Einzelwege, z.B. X 10 mm + Y 5 mm = 15 mm),
                        es gibt einen gemeinsamen Schmierimpuls

Laden in der .hal-Datei:
    # getrennt je Achse
    loadusr -Wn lubedist python3 lubedist.py --channels x,y,z \
            --distance 1000 --pulse-time 0.5
    # Summe aller Achsen
    loadusr -Wn lubedist python3 lubedist.py --channels x,y,z --mode sum \
            --distance 3000 --pulse-time 0.5

Pins:
  Je Achse <a> aus --channels (in beiden Modi):
    lubedist.<a>.pos            float in   Positionssignal (z.B. axis.x.pos-cmd)

  Je Zaehler <c> (mode separate: <c> = Achsname, mode sum: <c> = 'sum'):
    lubedist.<c>.interval       float in   Schmierstrecke (Default: --distance)
    lubedist.<c>.pulse-time     float in   Impulsdauer in s (Default: --pulse-time)
    lubedist.<c>.trigger        bit   in   steigende Flanke = sofort schmieren
    lubedist.<c>.reset          bit   in   steigende Flanke = Zaehler auf 0
    lubedist.<c>.pulse          bit   out  Ausgang -> Schmierventil/Pumpe
    lubedist.<c>.distance       float out  Weg seit letzter Schmierung
    lubedist.<c>.remaining      float out  Restweg bis zur naechsten Schmierung

  Global:
    lubedist.enable             bit   in   nur bei TRUE wird Weg gezaehlt (Default TRUE)
    lubedist.pulse-any          bit   out  ODER ueber alle Zaehler
"""

import argparse
import hal
import json
import os
import signal
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--name", default="lubedist", help="HAL-Komponentenname")
    ap.add_argument("-c", "--channels", default="x", help="Achsen, kommagetrennt, z.B. x,y,z")
    ap.add_argument("-m", "--mode", choices=["separate", "sum"], default="separate",
                    help="separate: Zaehler je Achse; sum: Summe aller Achsen in einem Zaehler")
    ap.add_argument("-d", "--distance", type=float, default=1000.0,
                    help="Schmierstrecke in Maschineneinheiten (mm oder inch)")
    ap.add_argument("-t", "--pulse-time", type=float, default=0.5,
                    help="Dauer des Schmierimpulses in Sekunden")
    ap.add_argument("-s", "--state", default="lubedist_state.json",
                    help="Datei fuer den persistenten Wegzaehler")
    ap.add_argument("--save-interval", type=float, default=5.0,
                    help="Speicherintervall in Sekunden (max. Verlust bei Stromausfall)")
    ap.add_argument("--cycle", type=float, default=0.01,
                    help="Abtastzeit in Sekunden")
    args = ap.parse_args()

    chans = [c.strip() for c in args.channels.split(",") if c.strip()]
    sum_mode = (args.mode == "sum")
    # Zaehler: je Achse einer, oder ein gemeinsamer Zaehler 'sum'
    counters = ["sum"] if sum_mode else chans
    state_path = os.path.abspath(args.state)

    # ---------- Zustand laden ----------
    acc = {}
    try:
        with open(state_path) as f:
            data = json.load(f)
        for k in counters:
            acc[k] = float(data.get(k, 0.0))
    except (IOError, OSError, ValueError):
        pass
    for k in counters:
        acc.setdefault(k, 0.0)

    def save():
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(acc, f)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, state_path)  # atomar: nie eine halb geschriebene Datei

    # ---------- HAL-Pins ----------
    h = hal.component(args.name)
    h.newpin("enable", hal.HAL_BIT, hal.HAL_IN)
    h.newpin("pulse-any", hal.HAL_BIT, hal.HAL_OUT)
    for c in chans:
        h.newpin(c + ".pos", hal.HAL_FLOAT, hal.HAL_IN)
    for k in counters:
        h.newpin(k + ".interval", hal.HAL_FLOAT, hal.HAL_IN)
        h.newpin(k + ".pulse-time", hal.HAL_FLOAT, hal.HAL_IN)
        h.newpin(k + ".trigger", hal.HAL_BIT, hal.HAL_IN)
        h.newpin(k + ".reset", hal.HAL_BIT, hal.HAL_IN)
        h.newpin(k + ".pulse", hal.HAL_BIT, hal.HAL_OUT)
        h.newpin(k + ".distance", hal.HAL_FLOAT, hal.HAL_OUT)
        h.newpin(k + ".remaining", hal.HAL_FLOAT, hal.HAL_OUT)
    h.ready()

    # Defaults (per 'setp' in der HAL-Datei ueberschreibbar)
    h["enable"] = True
    for k in counters:
        h[k + ".interval"] = args.distance
        h[k + ".pulse-time"] = args.pulse_time

    # ---------- sauberes Beenden ----------
    running = [True]

    def stop(signum, frame):
        running[0] = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    last_pos = dict((c, None) for c in chans)
    pulse_end = dict((k, 0.0) for k in counters)
    prev_trig = dict((k, False) for k in counters)
    prev_reset = dict((k, False) for k in counters)
    last_save = time.time()
    saved = dict(acc)

    try:
        while running[0]:
            now = time.time()
            en = bool(h["enable"])
            any_pulse = False
            force_save = False

            # Wege aller Achsen aufnehmen (je Achse oder in die Summe)
            for c in chans:
                pos = h[c + ".pos"]
                # Sprung beim ersten Sample bzw. waehrend enable=FALSE nicht mitzaehlen
                if en and last_pos[c] is not None:
                    acc["sum" if sum_mode else c] += abs(pos - last_pos[c])
                last_pos[c] = pos

            for k in counters:
                # Reset (steigende Flanke)
                r = bool(h[k + ".reset"])
                if r and not prev_reset[k]:
                    acc[k] = 0.0
                    force_save = True
                prev_reset[k] = r

                # manuelle Schmierung (steigende Flanke)
                t = bool(h[k + ".trigger"])
                if t and not prev_trig[k]:
                    pulse_end[k] = now + h[k + ".pulse-time"]
                    acc[k] = 0.0
                    force_save = True
                prev_trig[k] = t

                # automatische Schmierung nach Strecke
                iv = h[k + ".interval"]
                if iv > 0 and acc[k] >= iv and now >= pulse_end[k]:
                    pulse_end[k] = now + h[k + ".pulse-time"]
                    acc[k] = acc[k] % iv  # Restweg behalten, kein Mehrfachimpuls
                    force_save = True

                active = now < pulse_end[k]
                h[k + ".pulse"] = active
                any_pulse = any_pulse or active
                h[k + ".distance"] = acc[k]
                h[k + ".remaining"] = max(iv - acc[k], 0.0) if iv > 0 else 0.0

            h["pulse-any"] = any_pulse

            # Zustand sichern (nur wenn geaendert)
            if (force_save or now - last_save >= args.save_interval) and acc != saved:
                save()
                saved = dict(acc)
                last_save = now
            elif force_save or now - last_save >= args.save_interval:
                last_save = now

            time.sleep(args.cycle)
    finally:
        try:
            save()
        finally:
            h.exit()


if __name__ == "__main__":
    main()
