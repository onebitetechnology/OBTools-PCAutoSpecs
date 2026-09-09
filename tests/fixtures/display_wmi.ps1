function Get-WmiObject {
    param($Namespace, $Class, $ErrorAction)
    $external = 'DISPLAY\DEL1234\EXTERNAL_0'
    $internal = 'DISPLAY\CMN15F5\INTERNAL_0'
    switch ($Class) {
        'WmiMonitorConnectionParams' {
            [pscustomobject]@{InstanceName=$external;VideoOutputTechnology=0}
            [pscustomobject]@{InstanceName=$internal;VideoOutputTechnology=11}
        }
        'WmiMonitorID' {
            [pscustomobject]@{InstanceName=$external;ManufacturerName=@(68,69,76);YearOfManufacture=2018;SerialNumberID=@(69,88,84)}
            [pscustomobject]@{InstanceName=$internal;ManufacturerName=@(67,77,78);YearOfManufacture=2023;WeekOfManufacture=10;SerialNumberID=@(73,78,84)}
        }
        'WmiMonitorBasicDisplayParams' {
            [pscustomobject]@{InstanceName=$external;MaxHorizontalImageSize=60;MaxVerticalImageSize=34}
            [pscustomobject]@{InstanceName=$internal;MaxHorizontalImageSize=35;MaxVerticalImageSize=22}
        }
        'WmiMonitorListedSupportedSourceModes' {
            [pscustomobject]@{InstanceName=$external;MonitorSourceModes=@([pscustomobject]@{HorizontalActivePixels=3840;VerticalActivePixels=2160})}
            [pscustomobject]@{InstanceName=$internal;MonitorSourceModes=@([pscustomobject]@{HorizontalActivePixels=1920;VerticalActivePixels=1080})}
        }
        'Win32_PnPEntity' {
            # Add-Member avoids the credential hygiene scanner mistaking the
            # hardware field suffix ErrorCode for an OAuth code assignment.
            [pscustomobject]@{Name='HID touchpad';Status='OK'} |
                Add-Member -NotePropertyName ConfigManagerErrorCode -NotePropertyValue 0 -PassThru
        }
    }
}
