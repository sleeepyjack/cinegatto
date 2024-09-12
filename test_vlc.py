#!/usr/bin/env python3

from cinegatto.util import display_control
import time

display_control.turn_off_screen()

time.sleep(5)

display_control.turn_on_screen()

# import vlc
# import yt_dlp

# # Replace with your YouTube URL
# youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# # Function to get the best video URL using yt-dlp
# def get_youtube_stream_url(youtube_url):
#     ydl_opts = {
#         'format': 'best',  # Choose the best quality available
#         'noplaylist': True,  # Do not download playlists, only the video
#     }

#     with yt_dlp.YoutubeDL(ydl_opts) as ydl:
#         info_dict = ydl.extract_info(youtube_url, download=False)
#         video_url = info_dict['url']
#         return video_url

# # Get the stream URL from YouTube
# video_url = get_youtube_stream_url(youtube_url)

# # Create an instance of VLC player
# player = vlc.MediaPlayer(video_url)

# # Set fullscreen mode
# player.set_fullscreen(True)

# # Start playing the video
# print("Starting YouTube video...")
# player.play()

# # Let it play indefinitely
# while True:
#     pass  # Keep the script running to allow VLC to play

