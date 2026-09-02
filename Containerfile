FROM python

ARG CARDS_FILENAME=default-cards-20260901210543.jsonl
ARG CARDS_URL=https://data.scryfall.io/default-cards/$CARDS_FILENAME.gz
ARG SINGLES_URL=https://downloads.s3.cardmarket.com/productCatalog/productList/products_singles_1.json

WORKDIR /opt
RUN wget --quiet $CARDS_URL \
	&& gunzip $CARDS_FILENAME.gz
RUN wget --quiet $SINGLES_URL
COPY *.html *.json *.py .
RUN /opt/map_missing_cardmarket_ids.py --cards $CARDS_FILENAME
RUN /opt/build_review_ui.py \
	&& ln --symbolic review_ui.html index.html

EXPOSE 8000
CMD [ "/opt/review_server.py", "--host", "0.0.0.0", "--port", "8000" ]
