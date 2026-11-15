"""Expose the analytics service assembled by the application bootstrap."""

from fastapi import Request

from benefitfinder.application.analytics import AnalyticsService


def get_analytics_service(request: Request) -> AnalyticsService:
    return request.app.state.analytics_service
