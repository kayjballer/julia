[app]
title = Julia
package.name = julia
package.domain = org.jacques
source.dir = .
source.include_exts = py,html,js,css,png,json
source.include_patterns = web/*
version = 0.1
requirements = python3,plyer,pyjnius,android
orientation = portrait
fullscreen = 1
android.permissions = INTERNET,RECORD_AUDIO
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
p4a.bootstrap = webview
p4a.port = 5000

[buildozer]
log_level = 2
warn_on_root = 1
