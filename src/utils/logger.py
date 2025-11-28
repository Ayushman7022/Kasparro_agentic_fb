import logging
import os
from datetime import datetime

def get_logger(run_id: str, logs_dir: str = "logs") -> logging.Logger:
    os.makedirs(logs_dir, exist_ok=True)

    logger = logging.getLogger(run_id)

    if not logger.handlers:  # prevent duplicate handlers
        logger.setLevel(logging.DEBUG)

        log_path = os.path.join(logs_dir, f"run_{run_id}.log")
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # OPTIONAL: also log to console (info level)
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        logger.addHandler(console)

    return logger
