# cinegatte - A Cinema for Cats

## Scenario
Our cats like to watch videos of trail cams and webcams showing wildlife like birds or other small animals. There are many videos of that kind readily available on youtube - some of them spanning several hours. I've curated a public youtube playlist for this.

## Hardware and Setup
I have a RaspberryPi 5 connected to a 1080p PC monitor via HDMI. 

I would like to **minimally** provision the Pi, i.e., bootstrap a OS image, maybe setup network connection (WiFi for instance), and SSH if required. I really don't want to install a Desktop environment for this - it should be more or less "headless" (also no keyboard or mouse attached). The Pi does not run any other custom services - it only exists to serve cinegatto.

## Idea
Use the Pi to boot into cinegatto automatically after boot and start to play a random video in fullscreen from the playlist. Play videos from the playlist in an infinite loop until.

## Features
- Provide a config file (JSON) to make cinegattos behavior customizable (youtube playlist address, monitor resolution (maybe also auto detect), etc.)
- Provide a small local website (also mobile optimized) with an UI to interact with cinegatto (play/pause, next/previous, random shuffle, logs, etc.). This should ideally be a REST API so I can in the future add buttons over CPIO for instance. The server only runs in local LAN so no user auth or security is required for the API.
- When Cinegatto is set to "pause" the monitor should be shut off/ standby to not consume too much energy in idle state
- Setup from "baremetal" Pi installation to getting cinegatto running and installed as the bootup service should be automatic. Ideally, just download the repo or something and provision in on click/script.
- Rigorous logging. We will be using log entries for agentic debugging so any helpful information at log level "debug" or "trace" will help.
- It should not only optionally select a random video from the playlist, but also optionally select a random starting point in the video. Most of the videos run very long so this is fine as it brings in variation which is what the cats crave.
- Ideally the core functionality can be tested locally on my Macbook instead of having to copy over every code change to the Pi. This makes agentic development much easier.
- What else comes to mind?
