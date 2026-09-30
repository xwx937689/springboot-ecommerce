' 开机自动拉起电商后端栈（隐藏窗口运行，无需管理员权限）
' 放置于 shell:startup 文件夹即可在登录时自动执行
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\springboot-ecommerce\start-stack.ps1""", 0, False
