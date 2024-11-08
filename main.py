import asyncio
import websockets
import json
import edge_tts
import pygame
import random
import os
import re
import logging
import datetime

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load configuration from config.json
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Initialize pygame mixer
pygame.mixer.init()

# Ensure output directory exists
os.makedirs(config["output_directory"], exist_ok=True)


def replace_strings(text, replacements):
    """
    Replace substrings in the text based on a dictionary of replacements.

    :param text: The input text.
    :param replacements: A dictionary where keys are substrings to be replaced and values are the replacements.
    :return: The text with replacements applied.
    """
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(key) for key in replacements.keys()) + r")\b"
    )
    return pattern.sub(lambda x: replacements[x.group()], text)


def get_random_voice(gender, last_voice=None):
    """
    Get a random voice from the list of available voices for the specified gender.

    :param gender: The gender of the speaker ('male' or 'female').
    :param last_voice: The last used voice to avoid repetition.
    :return: A randomly selected voice.
    """
    available_voices = config["voices"].get(gender.lower(), config["voices"]["male"])
    if last_voice:
        available_voices = [voice for voice in available_voices if voice != last_voice]
    return random.choice(available_voices)


def get_random_pitch(race):
    """
    Get a random pitch value based on the speaker's race.

    :param race: The race of the speaker (e.g., 'Lalafell', 'Roegadyn').
    :return: A pitch value as a string.
    """
    if race == "Lalafell":
        pitch = random.randint(10, 20)
    elif race == "Roegadyn":
        pitch = random.randint(-20, -10)
    else:
        pitch = random.randint(-10, 10)
    return f"{pitch:+}"


def cleanup_old_files(directory, max_files):
    """
    Remove the oldest files in the directory if the number of files exceeds the max_files limit.

    :param directory: Directory to check for files.
    :param max_files: Maximum number of files to retain.
    """
    files = [os.path.join(directory, f) for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    if len(files) > max_files:
        files.sort(key=os.path.getmtime)
        old_files = files[:-max_files]  
        for file in old_files:
            os.remove(file)
            logging.info(f"Removed old file: {file}")


async def speak_text(text, voice, pitch):
    """
    Generate speech from text using the specified voice and pitch, and play it.

    :param text: The text to be spoken.
    :param voice: The TTS voice to use.
    :param pitch: The pitch adjustment for the voice.
    """
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%d%m%H%M%S") + f"{int(now.microsecond/10000):02d}"
    filename = os.path.join(config["output_directory"], f"{timestamp}.mp3")
    rate = "+25%"
    communicate = edge_tts.Communicate(text, voice=voice, pitch=f"{pitch}Hz", rate=rate)
    await communicate.save(filename)
    pygame.mixer.music.load(filename)
    pygame.mixer.music.play()

  
    cleanup_old_files(config["output_directory"], config["max_output_files"])


async def handle_speaker_info(speaker_info, speaker_file_path):
    """
    Handle the persistence of speaker information in a JSON file.

    :param speaker_info: A dictionary containing speaker information (name, gender, voice, pitch, race).
    :param speaker_file_path: Path to the JSON file where speaker details are stored.
    """
    try:
        with open(speaker_file_path, "r") as file:
            speaker_data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        speaker_data = []

  
    if not any(
        s["name"] == speaker_info["name"]
        and s["gender"] == speaker_info["gender"]
        and s["race"] == speaker_info["race"]
        for s in speaker_data
    ):
        speaker_data.append(speaker_info)
        with open(speaker_file_path, "w") as file:
            json.dump(speaker_data, file, indent=4)
        logging.info(f"Speaker created: {speaker_info['name']} with gender {speaker_info['gender']} and race {speaker_info['race']}")


async def listen():
    """
    Connect to the WebSocket server and listen for incoming messages to process.

    This function runs indefinitely, reconnecting automatically if the connection is lost.
    """
    uri = config["websocket_uri"]
    last_voice = None
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                logging.info("Connected to the WebSocket server.")
                while True:
                    try:
                        message = await websocket.recv()
                    except asyncio.CancelledError:
                        logging.info("Task cancelled.")
                        return

                    data = json.loads(message)

                    if data.get("Type") == "Say":
                        speaker_name = data.get("Speaker", "")
                        gender = data.get("Voice", {}).get("Name", "Male")
                        race = data.get("Race", "Unknown")

                        speaker_file_path = config["speaker_file_path"]
                        try:
                            with open(speaker_file_path, "r") as file:
                                speaker_data = json.load(file)
                        except (FileNotFoundError, json.JSONDecodeError):
                            speaker_data = []

                      
                        speaker = next(
                            (s for s in speaker_data if s["name"] == speaker_name and s["race"] == race),
                            None,
                        )

                        if speaker:
                            voice = speaker["voice"]
                            pitch = speaker["pitch"]
                        else:
                            voice = get_random_voice(gender, last_voice)
                            last_voice = voice
                            pitch = get_random_pitch(race)
                            speaker_info = {
                                "name": speaker_name,
                                "gender": gender,
                                "race": race,
                                "voice": voice,
                                "pitch": pitch,
                            }
                            await handle_speaker_info(speaker_info, speaker_file_path)

                        payload = data.get("Payload", "").lower()
                        payload = replace_strings(payload, config["string_replacements"])
                        if payload:
                            await speak_text(payload, voice, pitch)

                    elif data.get("Type") == "Cancel":
                        pygame.mixer.music.stop()
        except websockets.ConnectionClosed:
            logging.warning("Connection closed. Attempting to reconnect...")
            await asyncio.sleep(5)
            continue
        except asyncio.CancelledError:
            logging.info("Task cancelled. Exiting the loop.")
            return
        except ConnectionRefusedError:
            logging.error("Connection refused, server might be down. Retrying in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Unexpected error: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)


async def main():
    """
    Main entry point for the script. Starts the WebSocket listener.
    """
    try:
        await listen()
    except asyncio.CancelledError:
        logging.info("Main task cancelled. Exiting gracefully.")
    finally:
        pygame.quit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Script interrupted by user. Exiting.")