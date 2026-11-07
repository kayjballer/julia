[app]
title = Niko
package.name = julia
package.domain = org.jacques
source.dir = .
source.include_exts = py,html,js,css,png,json
source.include_patterns = web/*
version = 0.2
requirements = python3,pyjnius,android,certifi
orientation = portrait
fullscreen = 1
android.permissions = INTERNET,RECORD_AUDIO,FOREGROUND_SERVICE,FOREGROUND_SERVICE_MICROPHONE,POST_NOTIFICATIONS
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.add_libs_arm64_v8a = libs/arm64-v8a/*.so
android.add_src = java_src
android.accept_sdk_license = True
p4a.branch = develop
p4a.bootstrap = webview
p4a.port = 5000
services = julia:service.py:foreground:sticky:foregroundServiceType=microphone

[buildozer]
log_level = 2
warn_on_root = 1
