FROM python:3.7-stretch
WORKDIR /app

# Install non-chai dependencies
COPY ./requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Install eventlet production server if production build
ARG BUILD_ENV
RUN if [ "$BUILD_ENV" = "production" ] ; then pip install eventlet ; fi

# Clone chai code
ARG OVERCOOKED_URL
ARG OVERCOOKED_BRANCH
RUN git clone $OVERCOOKED_URL --branch $OVERCOOKED_BRANCH --single-branch /overcooked_ai
ARG HARL_URL
ARG HARL_BRANCH
RUN git clone $HARL_URL --branch $HARL_BRANCH --single-branch /human_aware_rl

# Dummy data_dir so things don't break
RUN echo "import os; DATA_DIR=os.path.abspath('.')" >> /human_aware_rl/human_aware_rl/data_dir.py

# Install chai dependencies
RUN pip install -e /overcooked_ai
RUN pip install -e /human_aware_rl

RUN apt-get update
RUN apt-get install -y libgl1-mesa-dev

# Copy over remaining files
COPY ./static ./static
COPY ./*.py ./
ARG GRAPHICS
COPY ./graphics/$GRAPHICS ./static/js/graphics.js
COPY ./config.json ./config.json

RUN mkdir ./static/overcooked_visualizations_web
RUN cp /overcooked_ai/src/overcooked_ai_py/visualization/web/* ./static/overcooked_visualizations_web

# Set environment variables that will be used by app.py
ENV HOST 0.0.0.0
ENV PORT 5000
ENV CONF_PATH config.json

# Do the thing
EXPOSE 5000
CMD ["python", "-u", "app.py"]