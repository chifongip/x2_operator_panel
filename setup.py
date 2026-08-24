from setuptools import find_packages, setup


package_name = "x2_operator_panel"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml", "README.md"]),
        ("share/" + package_name + "/launch", ["launch/operator_panel.launch.py"]),
        ("share/" + package_name + "/config", ["config/navigation_presets.yaml"]),
        ("share/" + package_name + "/static", [
            "x2_operator_panel/static/index.html",
            "x2_operator_panel/static/app.js",
            "x2_operator_panel/static/style.css",
        ]),
    ],
    install_requires=["setuptools", "PyYAML", "websockets"],
    zip_safe=True,
    maintainer="oscar",
    maintainer_email="oscar.ip@lscm.hk",
    description="Local web operator panel for X2 manipulation and navigation.",
    license="BSD-3-Clause",
    entry_points={
        "console_scripts": [
            "operator_panel = x2_operator_panel.panel_server:main",
            "operator_panel_hash_password = x2_operator_panel.auth:main",
        ],
    },
)
