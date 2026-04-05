import tkinter as tk
from ui import AudioMirrorApp


def main():
    root = tk.Tk()
    app = AudioMirrorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()