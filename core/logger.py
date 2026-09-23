import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import traceback
from datetime import datetime

_error_logger = None


def get_error_logger():
    global _error_logger
    if _error_logger is not None:
        return _error_logger

    logger = logging.getLogger("DICOM_WatchDog_ErrorLogger")
    logger.setLevel(logging.ERROR)

    if not logger.handlers:
        try:
            from core.config_utils import get_app_error_log_path
            log_path = get_app_error_log_path()
            handler = RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8"
            )
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except Exception as e:
            print(f"Failed to setup file error logger: {e}", file=sys.stderr)

    _error_logger = logger
    return _error_logger


def log_error(message: str, exc=None):
    """Writes an error message and optional exception traceback to error.log with rotation."""
    try:
        logger = get_error_logger()
        if exc is not None:
            if isinstance(exc, BaseException):
                tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
                err_text = "".join(tb_lines).rstrip()
            else:
                err_text = str(exc)
            logger.error(f"{message}\n{err_text}")
        else:
            logger.error(message)
    except Exception as e:
        print(f"Failed in log_error: {e}", file=sys.stderr)


_ERROR_KEYWORDS = (
    "ошибка", "error", "failed", "не удалось",
    "исключение", "exception", "сбой", "traceback"
)


def _is_error_message(message: str) -> bool:
    if not message:
        return False
    msg_lower = message.lower()
    return any(keyword in msg_lower for keyword in _ERROR_KEYWORDS)


def log_message(output_field, message, replace_suffix=None):
    if not output_field:
        return

    # Auto-record error messages to error.log
    if _is_error_message(message):
        log_error(message)

    current_time = datetime.now().time().strftime('%H:%M')
    formatted_message = f'[{current_time}] - {message}\n'
    
    if hasattr(output_field, 'insertPlainText'):  # PyQt QPlainTextEdit
        if replace_suffix:
            text = output_field.toPlainText()
            lines = text.split('\n')
            replaced = False
            for i, line in enumerate(lines):
                if line.endswith(replace_suffix):
                    lines[i] = f'[{current_time}] - {message}'
                    replaced = True
                    break
            if replaced:
                output_field.setPlainText('\n'.join(lines))
                return
                
        cursor = output_field.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        output_field.setTextCursor(cursor)
        output_field.insertPlainText(formatted_message)
    elif hasattr(output_field, 'appendPlainText'):
        output_field.appendPlainText(message)
    else:
        print(formatted_message.strip())
