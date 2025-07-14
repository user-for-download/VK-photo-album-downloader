# VK Album Downloader

![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)

A robust, asynchronous command-line tool to download all photos from a single VK.com photo album.

This script is designed for performance and reliability, using modern Python features to handle large albums, network errors, and API quirks gracefully.

## Key Features

-   **High Performance**: Fully asynchronous using `httpx` and `aiofiles` for fast network I/O without blocking.
-   **Resilient**: Automatically retries failed requests with exponential backoff using `tenacity`. If a server is temporarily unavailable, the script won't fail.
-   **Concurrency Control**: Uses an `asyncio.Semaphore` to limit the number of concurrent downloads, preventing you from overwhelming the server or your own network.
-   **Resume-Safe Scraping**: Creates a `scraped_urls.txt` checkpoint file. If the script is interrupted during the URL scraping phase, it will resume where it left off on the next run, saving time and bandwidth.
-   **Robust Logging**: Provides clear, timestamped logging to both the console and a rotating log file (`download.log`) inside the album folder.
-   **Error Handling**: Failed download URLs are saved to `failed_downloads.txt` for easy review and retrying.
-   **User-Friendly**:
    -   Handles both desktop (`vk.com`) and mobile (`m.vk.com`) URLs automatically.
    -   Creates a neatly named folder for each album (e.g., `vk_album_-12345_67890`).
    -   Skips files that have already been downloaded, making it safe to re-run.

## Prerequisites

-   Python 3.8 or newer.
-   `pip` for installing packages.

## Installation

1.  **Clone or Download**
    Clone this repository or download the `vk_album_downloader.py` script and the `requirements.txt` file into a new directory.

    ```bash
    git clone https://github.com/user-for-download/VK-photo-album-downloader.git
    cd VK-photo-album-downloader
    ```

2.  **Create a Virtual Environment**
    It is highly recommended to use a virtual environment to keep dependencies isolated.

    *   **On Windows:**
        ```powershell
        python -m venv venv
        .\venv\Scripts\activate
        ```
    *   **On macOS / Linux:**
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```

3.  **Install Dependencies**
    With your virtual environment active, install the required libraries from the `requirements.txt` file.

    ```
    pip install -r requirements.txt
    ```
    You will need a `requirements.txt` (pip freeze) file with the following content:
    
    ```text
    aiofiles==24.1.0
    anyio==4.9.0
    certifi==2025.7.14
    h11==0.16.0
    h2==4.2.0
    hpack==4.1.0
    httpcore==1.0.9
    httpx==0.28.1
    hyperframe==6.1.0
    idna==3.10
    orjson==3.10.18
    sniffio==1.3.1
    tenacity==9.1.2
    typing_extensions==4.14.1
    ```

## Usage

The script is run from the command line. The only required argument is the album link.

### Basic Command

```bash
python vk_album_downloader.py --album-link "URL_OF_THE_ALBUM"
```
Or using the short flag:
```bash
python vk_album_downloader.py -u "URL_OF_THE_ALBUM"
```

### Examples

*   **Downloading a standard desktop album:**
    ```bash
    python vk_album_downloader.py -u "https://vk.com/album-119234111_30934111"
    ```

*   **Downloading from a mobile link (will be handled automatically):**
    ```bash
    python vk_album_downloader.py -u "https://m.vk.com/album-1192311111_3093411111?rev=1"
    ```

*   **Specifying a custom output directory:**
    ```bash
    python vk_album_downloader.py -u "..." -o "./MyFavoritePhotos"
    ```

*   **Getting help:**
    ```bash
    python vk_album_downloader.py --help
    ```

## Output File Structure

After running, the script will create a directory based on the album's ID. Inside this directory, you will find:

```
vk_album_-119234786_309345852/
├── 0001.jpg
├── 0002.jpg
├── ...
├── download.log             # Detailed log of all operations (scraping, downloads, errors).
├── scraped_urls.txt         # Checkpoint file with all successfully scraped image URLs.
└── failed_downloads.txt     # (Only if errors occur) A list of URLs that failed to download.
```

## How It Works

1.  **Parse & Normalize**: The script takes the input URL, converts any mobile `m.vk.com` link to its `vk.com` desktop equivalent, and extracts the unique album ID.
2.  **Scrape Phase**:
    -   It loads any pre-existing URLs from `scraped_urls.txt` to resume progress.
    -   It enters a loop, sending POST requests to the album URL with an increasing `offset` to load photo data in batches.
    -   Each new batch of unique URLs is immediately appended to the `scraped_urls.txt` checkpoint file.
    -   A randomized delay is used between requests to be polite to the server.
3.  **Download Phase**:
    -   Once all unique URLs are scraped, it creates a queue of download tasks.
    -   `asyncio.gather` runs these tasks concurrently, limited by a semaphore (`concurrent_downloads` in settings).
    -   Each download request includes the correct `Referer` header to appear legitimate.
    -   If a download fails, `tenacity` retries it several times. If it still fails, the URL is logged to `failed_downloads.txt`.

## License

This project is licensed under the MIT License.