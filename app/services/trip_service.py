class TripService:

    def create_trip(self, trip_data: dict):
        return {
            "message": "Trip request received",
            "trip": trip_data,
        }

