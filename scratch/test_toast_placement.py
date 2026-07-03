import os
import sys
import winotify

# Modify TEMPLATE in winotify to use ToastGeneric and appLogoOverride placement WITHOUT id attributes
winotify.TEMPLATE = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$Template = @"
<toast {launch} duration="{duration}">
    <visual>
        <binding template="ToastGeneric">
            <text><![CDATA[{title}]]></text>
            <text><![CDATA[{msg}]]></text>
            <image placement="appLogoOverride" src="{icon}" />
        </binding>
    </visual>
    <actions>
        {actions}
    </actions>
    {audio}
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)

$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Toast.Tag = "{tag}"
$Toast.Group = "{group}"

$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}")
$Notifier.Show($Toast);
"""

icon_path = os.path.abspath("src/folder_notification.png")
print(f"Using icon path: {icon_path}")

try:
    toast = winotify.Notification(
        app_id='DICOM WatchDog',
        title='Новое КТ: Иванов И.И.',
        msg='Обнаружена новая серия снимков',
        duration='short',
        icon=icon_path
    )
    toast.show()
    print("Toast shown successfully with custom appLogoOverride template")
except Exception as e:
    print(f"Failed to show toast: {e}")
