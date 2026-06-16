import time
import traceback


def safe_execute(func, retries=3, delay=5):

    for attempt in range(retries):

        try:
            return func()

        except Exception as e:

            print("ERROR OCCURRED:", e)

            traceback.print_exc()

            if attempt < retries - 1:

                print("Retrying in", delay, "seconds...")

                time.sleep(delay)

            else:

                print("Max retries reached.")