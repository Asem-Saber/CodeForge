from src.config import logger
from src.agent.graph import loop
from src.sandbox.manager import close_sandbox

if __name__ == "__main__":
    try:
        while True:
            try:
                user_input = input("How can I help you?\n")
                if user_input.lower() in ["exit", "quit"]:
                    break
                loop(user_input)
            except KeyboardInterrupt:
                break
            except EOFError:
                break
    finally:
        close_sandbox()
        logger.info("Sandbox closed.")
