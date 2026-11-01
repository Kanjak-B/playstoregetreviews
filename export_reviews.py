#!/usr/bin/env python3
"""Entrée historique conservée pour les exports CSV/JSON."""
import sys
from playstoregetreviews.main import main

if __name__ == '__main__':
    raise SystemExit(main(['export', *sys.argv[1:]]))
