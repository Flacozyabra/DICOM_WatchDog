from datetime import datetime


def log_message(output_field, message, replace_suffix=None):
    if not output_field:
        return
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
