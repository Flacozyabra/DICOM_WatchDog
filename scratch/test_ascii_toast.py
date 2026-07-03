import subprocess
import os

# Иконка из папки проекта - как в рабочем test_toast_debug.py
raw_path = os.path.abspath("src/folder_notification.png")
url_path = "file:///" + raw_path.replace("\\", "/")

print(f"Icon path: {url_path}")

ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$Template = @"
<toast duration="short">
    <visual>
        <binding template="ToastGeneric">
            <text><![CDATA[ASCII Title Test v2]]></text>
            <text><![CDATA[Icon from project src folder]]></text>
            <image placement="appLogoOverride" src="{url_path}" />
        </binding>
    </visual>
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)

$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Toast.Tag = "ASCIITagV2"
$Toast.Group = "DICOM WatchDog"

$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("DICOM WatchDog")
$Notifier.Show($Toast);
"""

cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
res = subprocess.run(cmd, capture_output=True, text=True)
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
