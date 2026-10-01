' Auto-start the e-commerce test stack at logon (hidden window, no admin needed).
' Place a copy in the shell:startup folder. ASCII only - VBScript reads ANSI/GBK.
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\springboot-ecommerce\start-stack.ps1""", 0, False
