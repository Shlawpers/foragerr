# config.py
import yaml
import os

# Determine the absolute path to the config.yaml file
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.yaml")

with open(CONFIG_FILE, "r") as f:
    config = yaml.safe_load(f)