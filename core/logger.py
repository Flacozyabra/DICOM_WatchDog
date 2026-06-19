from datetime import datetime


def log_message(output_field, message):
    if not output_field:
        return
    current_time = datetime.now().time().strftime('%H:%M')
    formatted_message = f'[{current_time}] - {message}\n'
    
    if hasattr(output_field, 'insertPlainText'):  # PyQt QPlainTextEdit
        cursor = output_field.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        output_field.setTextCursor(cursor)
        output_field.insertPlainText(formatted_message)
    else:
        print(formatted_message.strip())
