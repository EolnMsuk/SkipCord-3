import pyautogui
import time

# List of words to type
words = ["discord", "discords", "omeglestream", "elonmusk", "streaming"]

# Typing speed settings
# 80 WPM = ~5 characters per second (including spaces and delays)
# Delay between keystrokes: ~0.12 seconds (adjusted for realism)
delay_between_keys = 0
delay_after_word = 0  # Delay after each word before hitting Enter

# Wait for 5 seconds before starting
time.sleep(5)

# Function to type a word with delays
def type_word(word):
    for char in word:
        pyautogui.typewrite(char)  # Type one character
        time.sleep(delay_between_keys)  # Delay between keystrokes
    time.sleep(delay_after_word)  # Delay after the word
    pyautogui.press('enter')  # Hit Enter

# Type each word in the list
for word in words:
    type_word(word)
