from player import Player
from ui import UI


def main():
    player = Player()
    ui = UI(player)
    ui.run()


if __name__ == "__main__":
    main()