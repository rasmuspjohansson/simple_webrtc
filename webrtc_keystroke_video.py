this version does not work for video becuas the cv2.display hangs. but a merge between this code and the video_pygame.py should work!


import asyncio
import argparse
import cv2
import sys
import os
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.signaling import TcpSocketSignaling
from av import VideoFrame
from pynput import keyboard

class WebcamVideoTrack(VideoStreamTrack):
    """
    A video stream track that captures frames from the webcam using OpenCV.
    """
    def __init__(self, device=0):
        super().__init__()
        self.cap = cv2.VideoCapture(device)
        # Set the desired properties for the webcam.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            raise Exception("Failed to capture frame from webcam")
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

async def send_char(channel, char):
    try:
        channel.send(char)
    except Exception as e:
        print("Error sending char:", e)

def on_key_press(key, channel, loop, role, video_callback=None, renegotiating_flag=None, shutdown_callback=None):
    """
    Global keypress handler:
      - If "q" is pressed, triggers shutdown.
      - For the offer role, if "v" is pressed, triggers video start.
      - Otherwise, if a data channel exists, sends letters (offer) or digits (answer).
      - While renegotiation is in progress, other keys are skipped.
    """
    try:
        char = key.char
        if char:
            if char.lower() == "q":
                if shutdown_callback:
                    loop.call_soon_threadsafe(shutdown_callback)
                return
            if renegotiating_flag is not None and renegotiating_flag[0]:
                print("Negotiation in progress. Skipping key:", char)
                return
            if role == "offer" and char.lower() == "v":
                if video_callback:
                    loop.call_soon_threadsafe(video_callback)
                return  # Do not send "v" as a keystroke.
            if channel is not None and ((role == "offer" and char.isalpha()) or (role == "answer" and char.isdigit())):
                loop.call_soon_threadsafe(lambda: asyncio.create_task(send_char(channel, char)))
    except AttributeError:
        pass  # Ignore special keys

def setup_channel(channel):
    @channel.on("message")
    def on_message(message):
        print(f"Received: {message}")

async def display_video(track):
    """
    Continuously receives video frames from the track and displays them via OpenCV.
    Each frame's shape is printed. Press 'q' in the OpenCV window to exit immediately.
    """
    while True:
        try:
            frame = await track.recv()
        except Exception as e:
            print("Error receiving frame:", e)
            break
        img = frame.to_ndarray(format="bgr24")
        print("Frame received of shape:", img.shape)
        cv2.imshow("Received Video", img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("q pressed in video window. Exiting.")
            break
    cv2.destroyAllWindows()
    os._exit(0)

async def renegotiate(pc, signaling, renegotiating_flag):
    """
    On the offer side, after adding the video track, wait briefly then trigger
    renegotiation so that the answer side learns about the new track.
    """
    print("Starting renegotiation to add video track...")
    renegotiating_flag[0] = True
    await asyncio.sleep(0.5)
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await signaling.send(pc.localDescription)
    print("Renegotiation offer sent, waiting for answer...")
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)
    print("Renegotiation complete. Video stream active.")
    renegotiating_flag[0] = False

async def shutdown(pc, signaling):
    """
    Closes the peer connection and signaling, then forces process termination.
    """
    print("Shutting down...")
    await signaling.close()
    await pc.close()
    os._exit(0)

async def run(pc, signaling, role):
    loop = asyncio.get_running_loop()
    # Shared holder for the data channel.
    channel_holder = {"channel": None}
    # For the offer side, a flag to indicate if video streaming is active.
    video_active_flag = [False]
    # A flag to disable key sending during renegotiation.
    renegotiating_flag = [False]

    shutdown_callback = lambda: asyncio.create_task(shutdown(pc, signaling))
    video_callback = None

    if role == "offer":
        def start_video_stream():
            if video_active_flag[0]:
                print("Video streaming already active.")
                return
            print("Starting video stream from webcam...")
            video_active_flag[0] = True
            track = WebcamVideoTrack(device=0)
            pc.addTrack(track)
            asyncio.create_task(renegotiate(pc, signaling, renegotiating_flag))
        video_callback = start_video_stream

        # Create the data channel.
        channel = pc.createDataChannel("chat")
        channel_holder["channel"] = channel
        setup_channel(channel)
    else:  # Answer side.
        @pc.on("datachannel")
        def on_datachannel(channel):
            channel_holder["channel"] = channel
            setup_channel(channel)
        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                print("Received video track.")
                asyncio.create_task(display_video(track))

    # Start a global keyboard listener.
    listener = keyboard.Listener(
        on_press=lambda key: on_key_press(
            key,
            channel_holder["channel"],
            loop,
            role,
            video_callback,
            renegotiating_flag,
            shutdown_callback,
        )
    )
    listener.start()

    await signaling.connect()

    if role == "offer":
        # Initial negotiation.
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await signaling.send(pc.localDescription)
        print("Initial offer sent, waiting for answer...")
        answer = await signaling.receive()
        await pc.setRemoteDescription(answer)
        print("Connection established as offer role.")
        await asyncio.Future()  # Run indefinitely.
    else:
        # Answer side: run a dedicated signaling loop.
        async def signaling_loop():
            while True:
                obj = await signaling.receive()
                if isinstance(obj, RTCSessionDescription) and obj.type == "offer":
                    print("Received offer (initial or renegotiation).")
                    await pc.setRemoteDescription(obj)
                    answer = await pc.createAnswer()
                    await pc.setLocalDescription(answer)
                    await signaling.send(pc.localDescription)
                    print("Negotiation complete (answer sent).")
        await signaling_loop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC keystroke and video streaming example")
    parser.add_argument("--role", choices=["offer", "answer"], required=True, help="Role in the connection")
    parser.add_argument("--signaling-host", default="localhost", help="Signaling server hostname")
    parser.add_argument("--signaling-port", type=int, default=9999, help="Signaling server port")
    args = parser.parse_args()

    signaling = TcpSocketSignaling(args.signaling_host, args.signaling_port)
    pc = RTCPeerConnection()

    asyncio.run(run(pc, signaling, args.role))
