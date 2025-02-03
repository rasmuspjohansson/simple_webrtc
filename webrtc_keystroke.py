"""
This program establishes a WebRTC connection between two computers to transmit keystrokes.
One computer runs in "offer" mode, sending only letters, while the other runs in "answer" mode, sending only numbers.
The connection is facilitated using aiortc for WebRTC and a simple TCP-based signaling server.
"""

import asyncio
import json
import sys
import argparse
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
from aiortc.contrib.signaling import TcpSocketSignaling
from pynput import keyboard

def on_key_press(key, channel, loop, role):
    """Handles keypress events and sends the appropriate characters based on the role."""
    try:
        char = key.char  # Get the character representation of the key pressed
        if char:
            # Only send letters if in "offer" mode, and only send numbers if in "answer" mode
            if (role == "offer" and char.isalpha()) or (role == "answer" and char.isdigit()):
                if channel.readyState == "open":
                    print(f"Sent: {char}")
                    loop.call_soon_threadsafe(channel.send, char)  # Send the character via WebRTC channel
                else:
                    print("Warning: No active connection. Character not sent.")
    except AttributeError:
        pass  # Ignore special keys like Shift, Ctrl, etc.

async def run(pc, signaling, role):
    """Handles WebRTC connection setup and communication."""
    loop = asyncio.get_running_loop()
    
    def setup_channel(channel):
        """Configures the data channel to listen for incoming messages and handle keystrokes."""
        @channel.on("message")
        def on_message(message):
            print(f"Received: {message}")  # Print received messages
        
        # Start listening for keystrokes and process them based on the role
        listener = keyboard.Listener(on_press=lambda key: on_key_press(key, channel, loop, role))
        listener.start()
    
    if role == "offer":
        channel = pc.createDataChannel("chat")  # Create a data channel for communication
        setup_channel(channel)  # Setup message handling
    
    @pc.on("datachannel")
    def on_datachannel(channel):
        """Handles the event when the answer role receives a data channel from the offer role."""
        setup_channel(channel)
    
    await signaling.connect()  # Connect to the signaling server
    
    if role == "offer":

        offer = await pc.createOffer()  # Create WebRTC offer
        await pc.setLocalDescription(offer)  # Set local description
        await signaling.send(pc.localDescription)  # Send offer to signaling server
        print("Offer sent. Waiting for answer...")
        answer = await signaling.receive()  # Receive answer from remote peer
        await pc.setRemoteDescription(answer)  # Set remote description
        print("Connection established as offer role.")
    else:
        offer = await signaling.receive()  # Receive offer from remote peer
        await pc.setRemoteDescription(offer)  # Set remote description
        print("Offer received. Creating answer...")
        answer = await pc.createAnswer()  # Create WebRTC answer
        await pc.setLocalDescription(answer)  # Set local description
        await signaling.send(pc.localDescription)  # Send answer back to offer
    
    await asyncio.Future()  # Keep the connection alive

if __name__ == "__main__":
    """Parses command-line arguments and starts the WebRTC connection."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["offer", "answer"], required=True, help="Role in WebRTC connection")
    parser.add_argument("--signaling-host", default="localhost", help="Signaling server hostname")
    parser.add_argument("--signaling-port", type=int, default=9999, help="Signaling server port")
    args = parser.parse_args()

    signaling = TcpSocketSignaling(args.signaling_host, args.signaling_port)  # Initialize signaling server
    pc = RTCPeerConnection()  # Create WebRTC peer connection

    asyncio.run(run(pc, signaling, args.role))  # Run the WebRTC process

