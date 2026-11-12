# CS4250-Final-Project

## Overview
This project is a mini search engine that crawls CPP's Civil Engineering faculty research and content, and provides a GUI interface for searching professors based on text queries. The system will crawl starting from the base home page https://www.cpp.edu/engineering/ce/index.shtml, and then it will extract each faculty member's searchable content. The content is then processed and stored on MongoDB, where it will be indexed for retrieval. A query interface will then rank faculty pages by relevance, and it will return a link, faculty name, and text snippet for each result

## Tech Stack
- Python
- Requests
- BeautifulSoup
- PyMongo
