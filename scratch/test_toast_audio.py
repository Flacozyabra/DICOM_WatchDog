import subprocess
import os
import time

raw_path = os.path.abspath("src/folder_notification.png")
url_path = "file:///" + raw_path.replace("\\", "/")

def send_toast(title, msg, audio_tag):
    ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$Template = @"
<toast duration="short">
    <visual>
        <binding template="ToastGeneric">
            <text><![CDATA[{title}]]></text>
            <text><![CDATA[{msg}]]></text>
            <image placement="appLogoOverride" src="{url_path}" />
        </binding>
    </visual>
    {audio_tag}
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)

$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Toast.Tag = "{title.replace(' ', '')}"
$Toast.Group = "DICOM WatchDog"

$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("DICOM WatchDog")
$Notifier.Show($Toast);
"""
    cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    subprocess.run(cmd, capture_output=True, text=True)

print("Отправка Теста 1: Без тега audio вообще...")
send_toast("Тест 1 (Без тега audio)", "Проверяем, будет ли стандартный звук", "")
time.sleep(4)

print("Отправка Теста 2: С тегом audio Default...")
send_toast("Тест 2 (Audio Default)", "Проверяем стандартный звук через тег", '<audio src="ms-winsoundevent:Notification.Default" />')
time.sleep(4)

print("Отправка Теста 3: С тегом silent=true...")
send_toast("Тест 3 (Silent True)", "Проверяем тихий режим", '<audio silent="true" />')
time.sleep(4)

print("Тест завершен.")
