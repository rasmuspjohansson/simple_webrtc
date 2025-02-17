import asyncio
import argparse
import sys
import threading
import queue
import numpy as np
import pygame
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.signaling import TcpSocketSignaling
from av import VideoFrame

# --- Offer Side: Capture from Webcam using OpenCV ---

class WebcamVideoTrack(VideoStreamTrack):
    """
    A video track that continuously captures frames from the webcam using OpenCV.
    """
    def __init__(self, device=0):
        super().__init__()
        self.cap = cv2.VideoCapture(device)
        if not self.cap.isOpened():
            print(f"ERROR: Could not open webcam device {device}")
        else:
            print(f"WebcamVideoTrack: Webcam device {device} opened successfully.")
        # Set resolution and framerate.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        ret, frame = self.cap.read()
        if not ret:
            print("ERROR: Failed to capture frame from webcam")
            raise Exception("Failed to capture frame from webcam")
        print("WebcamVideoTrack: Captured frame of shape:", frame.shape)
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

# --- Answer Side: Display video using pygame ---

def pygame_display_loop(frame_queue):
    """
    Runs in a separate thread. Creates a pygame window and continuously
    displays frames taken from the frame_queue.
    """
    pygame.init()
    screen = pygame.display.set_mode((640, 480))
    pygame.display.set_caption("Received Video")
    clock = pygame.time.Clock()
    running = True
    while running:
        # Process pygame events.
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        try:
            # Get a frame from the queue; if none available, continue.
            frame = frame_queue.get(timeout=0.01)
        except queue.Empty:
            frame = None

        if frame is not None:
            # frame is a numpy array in BGR order.
            # Convert BGR to RGB:
            frame_rgb = frame[..., ::-1]
            # pygame expects the array shape as (width, height, channels) via transposition.
            surface = pygame.surfarray.make_surface(np.transpose(frame_rgb, (1, 0, 2)))
            screen.blit(surface, (0, 0))
            pygame.display.flip()
        clock.tick(30)
    pygame.quit()
    sys.exit(0)

async def display_video_pygame(track):
    """
    Asynchronously receives video frames from the track and pushes them into a
    thread-safe queue. A separate thread runs the pygame display loop.
    """
    frame_queue = queue.Queue()
    # Start the pygame display loop in a daemon thread.
    display_thread = threading.Thread(target=pygame_display_loop, args=(frame_queue,), daemon=True)
    display_thread.start()

    while True:
        try:
            frame = await track.recv()
        except Exception as e:
            print("Answer: Error receiving frame:", e)
            break
        img = frame.to_ndarray(format="bgr24")
        print("Answer: Received frame of shape:", img.shape)
        frame_queue.put(img)
        await asyncio.sleep(0.01)

# --- Signaling / Negotiation Functions ---

async def run_offer(pc, signaling):
    """
    Offer role: Immediately adds the webcam video track and negotiates with the answer.
    """
    print("Offer: Adding webcam video track...")
    video_track = WebcamVideoTrack(device=0)
    pc.addTrack(video_track)

    await signaling.connect()

    # Create and send the offer.
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    print("Offer: Sending offer...")
    await signaling.send(pc.localDescription)
    print("Offer: Offer sent, waiting for answer...")

    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)
    print("Offer: Connection established. Streaming video...")

    await asyncio.Future()  # Run indefinitely.

async def run_answer(pc, signaling):
    """
    Answer role: Waits for an offer, sends an answer, and displays the incoming video using pygame.
    """
    @pc.on("track")
    def on_track(track):
        if track.kind == "video":
            print("Answer: Received video track. Starting pygame display...")
            asyncio.create_task(display_video_pygame(track))

    await signaling.connect()

    print("Answer: Waiting for offer...")
    offer = await signaling.receive()
    print("Answer: Received offer.")
    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    print("Answer: Sending answer...")
    await signaling.send(pc.localDescription)
    print("Answer: Negotiation complete. Video streaming...")

    await asyncio.Future()  # Run indefinitely.

async def run(pc, signaling, role):
    if role == "offer":
        await run_offer(pc, signaling)
    else:
        await run_answer(pc, signaling)

def main():
    parser = argparse.ArgumentParser(
        description="WebRTC Video Streaming Example using Pygame for Display (Offer streams video continuously)"
    )
    parser.add_argument("--role", choices=["offer", "answer"], required=True,
                        help="Role in the connection (offer streams video; answer displays it)")
    parser.add_argument("--signaling-host", default="localhost",
                        help="Signaling server hostname")
    parser.add_argument("--signaling-port", type=int, default=9999,
                        help="Signaling server port")
    args = parser.parse_args()

    signaling = TcpSocketSignaling(args.signaling_host, args.signaling_port)
    pc = RTCPeerConnection()

    asyncio.run(run(pc, signaling, args.role))

if __name__ == "__main__":
    main()
